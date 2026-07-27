<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div v-if="successMessage" class="success-banner">{{ successMessage }}</div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budget.title') }}</h3>
        </div>
        <div class="budget-controls">
          <label class="budget-label" for="budget-slider">{{ t('restocking.budget.label') }}</label>
          <div class="budget-value">{{ formatCurrency(budget) }}</div>
          <input
            id="budget-slider"
            type="range"
            class="budget-slider"
            min="0"
            :max="maxBudget"
            step="1000"
            v-model.number="budget"
          />
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.budget.spent') }}</div>
          <div class="stat-value">{{ formatCurrency(recommendedTotal) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.budget.remaining') }}</div>
          <div class="stat-value">{{ formatCurrency(remainingBudget) }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.budget.itemCount') }}</div>
          <div class="stat-value">{{ recommendedItems.length }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations.title') }}</h3>
        </div>

        <div v-if="recommendedItems.length" class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.itemName') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.quantity') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineTotal') }}</th>
                <th>{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in recommendedItems" :key="item.sku">
                <td><strong>{{ item.sku }}</strong></td>
                <td>{{ translateProductName(item.name) }}</td>
                <td>
                  <span :class="['badge', item.trend]">{{ t(`trends.${item.trend}`) }}</span>
                </td>
                <td>{{ item.quantity }}</td>
                <td>{{ formatCurrency(item.unitCost) }}</td>
                <td><strong>{{ formatCurrency(item.lineTotal) }}</strong></td>
                <td>{{ t('restocking.days', { count: getLeadTime(item.trend) }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-state">{{ t('restocking.recommendations.empty') }}</div>

        <div v-if="skippedSkus.length" class="skipped-note">
          {{ t('restocking.recommendations.skippedNote', { skus: skippedSkus.join(', ') }) }}
        </div>

        <div v-if="placeOrderError" class="error">{{ placeOrderError }}</div>

        <div class="place-order-row">
          <button
            class="place-order-btn"
            :disabled="recommendedItems.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../api'
import { useFilters } from '../composables/useFilters'
import { useI18n } from '../composables/useI18n'
import { formatCurrency as formatCurrencyUtil } from '../utils/currency'

// Trend -> estimated lead time in days (display only; backend computes authoritative lead times)
const LEAD_TIME_DAYS = {
  increasing: 5,
  stable: 10,
  decreasing: 14
}

// Trend priority ranking used to sort restock candidates (lower rank = higher priority)
const TREND_RANK = {
  increasing: 0,
  stable: 1,
  decreasing: 2
}

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName } = useI18n()
    const { selectedLocation, selectedCategory, getCurrentFilters } = useFilters()

    const loading = ref(true)
    const error = ref(null)
    const placeOrderError = ref(null)
    const submitting = ref(false)
    const successMessage = ref(null)

    const allForecasts = ref([])
    const inventoryItems = ref([])
    const budget = ref(0)
    const budgetInitialized = ref(false)

    // Map of SKU -> unit cost, built from inventory data (filter-scoped)
    const unitCostBySku = computed(() => {
      const map = new Map()
      inventoryItems.value.forEach(item => {
        map.set(item.sku, item.unit_cost)
      })
      return map
    })

    // Resolve a unit cost for a forecast item: prefer the matching inventory item's
    // unit_cost (kept as an optional override when filters narrow inventory results),
    // otherwise fall back to the cost carried directly on the forecast itself.
    const resolveUnitCost = (forecast) => {
      if (unitCostBySku.value.has(forecast.item_sku)) {
        return unitCostBySku.value.get(forecast.item_sku)
      }
      return forecast.unit_cost
    }

    // All forecast items that can be priced (inventory override or the forecast's own
    // unit_cost), turned into line candidates with quantity/unitCost/lineTotal precomputed.
    const priceableCandidates = computed(() => {
      return allForecasts.value
        .filter(f => !!resolveUnitCost(f))
        .map(f => {
          const unitCost = resolveUnitCost(f)
          const quantity = f.forecasted_demand
          return {
            sku: f.item_sku,
            name: f.item_name,
            trend: f.trend,
            quantity,
            unitCost,
            lineTotal: quantity * unitCost
          }
        })
    })

    // Forecast SKUs with no usable cost at all (missing/zero/undefined on both the
    // forecast and any matching inventory item) - safety net, should normally be empty.
    const skippedSkus = computed(() => {
      return allForecasts.value
        .filter(f => !resolveUnitCost(f))
        .map(f => f.item_sku)
    })

    // Max budget = cost of restocking every priceable candidate (selects everything)
    const maxBudget = computed(() => {
      const total = priceableCandidates.value.reduce((sum, c) => sum + c.lineTotal, 0)
      return total > 0 ? Math.ceil(total) : 1000
    })

    /*
     * Recommendation algorithm ("trend priority only"):
     * 1. Sort all priceable candidates by trend priority - increasing demand items
     *    first, then stable, then decreasing - since increasing-demand items are the
     *    most urgent to restock. Ties are broken by forecasted demand (descending).
     * 2. Greedily walk the sorted list keeping a running "remaining budget" total.
     *    A candidate is included if its lineTotal fits within what's left of the
     *    budget; otherwise it is skipped and we keep looking further down the list
     *    (we do NOT stop at the first item that doesn't fit, since a cheaper,
     *    lower-priority item later in the list may still fit the remaining budget).
     */
    const recommendedItems = computed(() => {
      const sorted = [...priceableCandidates.value].sort((a, b) => {
        const rankDiff = TREND_RANK[a.trend] - TREND_RANK[b.trend]
        if (rankDiff !== 0) return rankDiff
        return b.quantity - a.quantity
      })

      const selected = []
      let remaining = budget.value

      for (const candidate of sorted) {
        if (candidate.lineTotal <= remaining) {
          selected.push(candidate)
          remaining -= candidate.lineTotal
        }
      }

      return selected
    })

    const recommendedTotal = computed(() => {
      return recommendedItems.value.reduce((sum, item) => sum + item.lineTotal, 0)
    })

    const remainingBudget = computed(() => {
      return budget.value - recommendedTotal.value
    })

    const formatCurrency = (value) => {
      return formatCurrencyUtil(value, currentCurrency.value)
    }

    const getLeadTime = (trend) => {
      return LEAD_TIME_DAYS[trend] || 0
    }

    const loadData = async () => {
      try {
        loading.value = true
        error.value = null
        const filters = getCurrentFilters()

        const [forecastsData, inventoryData] = await Promise.all([
          api.getDemandForecasts(),
          api.getInventory({
            warehouse: filters.warehouse,
            category: filters.category
          })
        ])

        allForecasts.value = forecastsData
        inventoryItems.value = inventoryData

        // Initialize budget to roughly half of maxBudget on first load only
        if (!budgetInitialized.value) {
          budget.value = Math.round(maxBudget.value / 2)
          budgetInitialized.value = true
        }
      } catch (err) {
        error.value = 'Failed to load restocking data: ' + err.message
      } finally {
        loading.value = false
      }
    }

    watch([selectedLocation, selectedCategory], () => {
      loadData()
    })

    // Clear the success banner whenever the user adjusts the budget again
    watch(budget, () => {
      successMessage.value = null
    })

    const placeOrder = async () => {
      placeOrderError.value = null
      submitting.value = true
      try {
        const result = await api.createRestockOrder({
          budget: budget.value,
          items: recommendedItems.value.map(item => ({
            sku: item.sku,
            name: item.name,
            quantity: item.quantity,
            unit_price: item.unitCost,
            trend: item.trend
          }))
        })
        successMessage.value = t('restocking.success', { orderNumber: result.order_number })
      } catch (err) {
        placeOrderError.value = 'Failed to place restock order: ' + err.message
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadData)

    return {
      t,
      translateProductName,
      loading,
      error,
      placeOrderError,
      submitting,
      successMessage,
      budget,
      maxBudget,
      recommendedItems,
      recommendedTotal,
      remainingBudget,
      skippedSkus,
      formatCurrency,
      getLeadTime,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-controls {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #475569;
}

.budget-value {
  font-size: 2rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
}

.budget-slider {
  width: 100%;
  height: 6px;
  border-radius: 4px;
  background: #e2e8f0;
  outline: none;
  -webkit-appearance: none;
  appearance: none;
  cursor: pointer;
  accent-color: #3b82f6;
}

.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #0f172a;
  border: 3px solid white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  transition: transform 0.15s ease;
}

.budget-slider::-webkit-slider-thumb:hover {
  transform: scale(1.1);
}

.budget-slider::-moz-range-thumb {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #0f172a;
  border: 3px solid white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
  cursor: pointer;
}

.budget-slider::-moz-range-track {
  height: 6px;
  border-radius: 4px;
  background: #e2e8f0;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
  font-size: 0.938rem;
}

.skipped-note {
  margin-top: 1rem;
  font-size: 0.813rem;
  color: #94a3b8;
  font-style: italic;
}

.success-banner {
  background: #d1fae5;
  border: 1px solid #a7f3d0;
  color: #065f46;
  padding: 1rem;
  border-radius: 8px;
  margin-bottom: 1.25rem;
  font-size: 0.938rem;
  font-weight: 500;
}

.place-order-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 1.25rem;
}

.place-order-btn {
  padding: 0.75rem 1.75rem;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  font-size: 0.938rem;
  cursor: pointer;
  transition: transform 0.2s ease, opacity 0.2s ease;
  white-space: nowrap;
}

.place-order-btn:hover:not(:disabled) {
  transform: translateY(-2px);
}

.place-order-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>

<template>
  <section class="page" data-module="settle">
    <header class="page-head">
      <div>
        <h2>作业结算管理</h2>
        <p class="page-desc">维护结算单，围绕结算单号、结算对象、对账员、结算周期做登记、筛选与收款确认；只有归属对账员能提交动作。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记结算单</button>
        <button class="btn" type="button" @click="exportRows">导出作业结算清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p v-if="session.readonly" class="scope-tip">
      当前账号「{{ session.operator }}」仅有查看权限，所有结算动作均不可提交；归属其他对账员的结算单同样只读。
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="notice" class="action-notice" :class="noticeTone">{{ notice }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <button v-if="column === '结算单号'" class="link" type="button" @click="openDetail(row)">
              {{ row[column] ?? '—' }}
            </button>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
          <td class="row-actions">
            <template v-if="canMutate(row)">
              <button
                v-for="action in actionsFor(row)"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <span v-else class="readonly-hint">仅查看</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无作业结算数据，可先登记结算单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条作业结算记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="detail" class="modal-mask" @click.self="closeDetail">
      <div class="modal-card" role="dialog" aria-modal="true" aria-label="结算单详情">
        <header class="modal-head">
          <strong>结算单详情 · {{ detail['结算单号'] }}</strong>
          <button class="link" type="button" @click="closeDetail">关闭</button>
        </header>
        <dl class="detail-grid">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detail[field] ?? '—' }}</dd>
          </template>
        </dl>
        <footer class="modal-foot">
          <span v-if="canMutate(detail)">归属对账员：{{ detail['对账员'] || '未指派' }}</span>
          <span v-else class="readonly-hint">当前账号对该结算单仅可查看</span>
        </footer>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/settle'
const columns = ['结算单号', '结算对象', '对账员', '结算周期', '作业量', '应收金额', '已收金额', '开票状态', '结算状态']
const actions = ['发起核对', '确认结算', '标记争议']
const detailFields = [
  ...columns,
  '收款确认人',
  '收款确认时间',
  '争议标记人',
  '争议标记时间',
]

const session = useSessionStore()
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const notice = ref('')
const noticeTone = ref<'ok' | 'warn'>('ok')
const detail = ref<Row | null>(null)
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const stats = computed(() => [
  { label: '待核对结算单', value: rows.value.filter((row) => row.status === '待核对').length },
  {
    label: '本月结算额',
    value: rows.value
      .filter((row) => row.status === '已收款')
      .reduce((sum, row) => sum + Number(row['应收金额'] ?? 0), 0),
  },
  { label: '争议单数', value: rows.value.filter((row) => row.status === '有争议').length },
])

function canMutate(row: Row) {
  // 只读账号一律不可改动；其余账号只有在结算单归属自己时才能提交。
  return !session.readonly && String(row['对账员'] ?? '') === session.operator
}

function actionsFor(row: Row) {
  // 延续既有对账习惯：已收款的单子只保留争议入口，其余状态可走原动作。
  return actions.filter((action) => !(action === '确认结算' && row.status === '已收款'))
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '结算单登记入口尚未接入审批流'
}

function closeDetail() {
  detail.value = null
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('结算单详情读取失败')
    }
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结算单详情读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, operator: session.operator } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload) {
      throw new Error(payload?.detail ?? '作业结算动作未生效，请稍后重试')
    }
    // 越权、状态不符等业务拒绝由服务端说明原因，前端原样呈现，不做本地状态改写。
    notice.value = payload.message ?? ''
    noticeTone.value = payload.ok ? 'ok' : 'warn'
    if (payload.ok) {
      await reload()
      if (detail.value && String(detail.value.id) === String(row.id) && payload.entry) {
        detail.value = payload.entry as Row
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '作业结算操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('结算单列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '作业结算列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.scope-tip {
  margin: 8px 0;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-left: 3px solid #b54708;
  border-radius: 6px;
  background: #fffaeb;
  color: #b54708;
}

.action-notice {
  margin: 8px 0;
  padding: 8px 12px;
  border-radius: 6px;
}

.action-notice.ok {
  border: 1px solid #abefc6;
  background: #f6fef9;
  color: #067647;
}

.action-notice.warn {
  border: 1px solid #fecdca;
  background: #fffbfa;
  color: #b42318;
}

.readonly-hint {
  color: var(--muted);
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}

.modal-card {
  width: min(640px, 92vw);
  max-height: 84vh;
  overflow: auto;
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.22);
}

.modal-head,
.modal-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.detail-grid {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 6px 14px;
  margin: 14px 0;
}

.detail-grid dt {
  color: var(--muted);
}

.detail-grid dd {
  margin: 0;
}
</style>

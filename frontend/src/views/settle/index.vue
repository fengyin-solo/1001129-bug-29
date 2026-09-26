<template>
  <section class="page" data-module="settle">
    <header class="page-head">
      <div>
        <h2>作业结算管理</h2>
        <p class="page-desc">维护结算单，围绕结算单号、结算对象、结算周期、作业量做登记、筛选与状态流转；收款确认仅归属对账员可提交。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记结算单</button>
        <button class="btn" type="button" @click="exportRows">导出作业结算清单</button>
      </div>
    </header>

    <p class="scope-hint" :class="{ locked: !store.isReconciler }">
      当前账号「{{ store.operator }}」
      <template v-if="store.isReconciler">：仅可对归属到本人的结算对象提交确认，其他结算单只读。</template>
      <template v-else>不是对账员账号，全部结算单仅可查看，不能确认收款或标记争议。</template>
    </p>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ formatCell(column, row[column]) }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="actionGate(action, row).disabled"
              :title="actionGate(action, row).reason"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无作业结算数据，可先登记结算单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条作业结算记录</span>
      <span v-if="message" :class="messageKind === 'error' ? 'error-text' : 'info-text'">{{ message }}</span>
    </footer>

    <!-- 收款确认 / 标记争议共用弹窗：越权与重复提交最终都由后端拒绝并说明原因 -->
    <div v-if="dialog.open" class="modal-mask" @click.self="closeDialog">
      <div class="modal-card">
        <h3 class="modal-title">
          {{ dialog.action === '确认结算' ? '确认收款' : '标记争议' }} · {{ dialog.row?.['结算单号'] }}
        </h3>
        <p class="modal-sub">
          结算对象：{{ dialog.row?.['结算对象'] }} ｜ 负责对账员：{{ ownerName(dialog.row) }} ｜ 操作账号：{{ store.operator }}
        </p>

        <template v-if="dialog.action === '确认结算'">
          <p class="modal-line">应收金额：{{ formatAmount(dialog.row?.['应收金额']) }}</p>
          <label class="modal-field">
            <span>收款金额（不填则沿用应收金额，确认后落库不可重复提交）</span>
            <input v-model="receiptAmount" type="number" min="0" step="0.01" placeholder="请输入实际收款金额" />
          </label>
        </template>
        <template v-else>
          <label class="modal-field">
            <span>争议原因（可留空，争议标记会同时记录操作人与时间）</span>
            <textarea v-model="disputeReason" rows="3" placeholder="请说明争议事由"></textarea>
          </label>
        </template>

        <div class="modal-actions">
          <button class="btn" type="button" :disabled="submitting" @click="closeDialog">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitDialog">
            {{ submitting ? '提交中…' : '提交' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 详情：直接读取后端单条明细，已收金额与列表同源，不会对不上 -->
    <div v-if="detailRow" class="modal-mask" @click.self="detailRow = null">
      <div class="modal-card wide">
        <h3 class="modal-title">结算单详情 · {{ detailRow['结算单号'] }}</h3>
        <dl class="detail-grid">
          <template v-for="field in detailFields" :key="field">
            <div v-if="detailRow[field] !== null && detailRow[field] !== undefined && detailRow[field] !== ''">
              <dt>{{ field }}</dt>
              <dd>{{ formatCell(field, detailRow[field]) }}</dd>
            </div>
          </template>
        </dl>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detailRow = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Cell = string | number | boolean | null
type Row = Record<string, Cell>

const ENDPOINT = '/api/settle'
const columns = ['结算单号', '结算对象', '对账员', '结算周期', '作业量', '应收金额', '已收金额', '开票状态', '结算状态']
const actions = ['发起核对', '确认结算', '标记争议']
const detailFields = [
  '结算单号', '结算对象', '对账员', '结算周期', '作业量',
  '应收金额', '已收金额', '开票状态', '结算状态',
  '核对发起人', '核对发起时间', '收款确认人', '收款确认时间',
  '争议登记人', '争议登记时间', '争议原因',
]

const store = useSessionStore()

const rows = ref<Row[]>([])
const total = ref(0)
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const message = ref('')
const messageKind = ref<'error' | 'info'>('info')
const submitting = ref(false)
const receiptAmount = ref('')
const disputeReason = ref('')
const detailRow = ref<Row | null>(null)

const dialog = reactive<{ open: boolean; action: string; row: Row | null }>({
  open: false,
  action: '',
  row: null,
})

const monthPrefix = (() => {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
})()

const stats = computed(() => [
  { label: '待核对结算单', value: rows.value.filter((row) => row['status'] === '待核对').length },
  {
    label: `本月实收（${monthPrefix}）`,
    value: formatAmount(
      rows.value
        .filter((row) => String(row['收款确认时间'] ?? '').startsWith(monthPrefix))
        .reduce((sum, row) => sum + (Number(row['已收金额']) || 0), 0),
    ),
  },
  { label: '争议单数', value: rows.value.filter((row) => row['status'] === '有争议').length },
])

function setMessage(kind: 'error' | 'info', text: string) {
  messageKind.value = kind
  message.value = text
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  setMessage('info', '结算单登记入口尚未接入审批流')
}

function ownerName(row: Row | null): string {
  if (!row) return '—'
  return store.ownerOf(String(row['结算对象'] ?? '')) ?? '未分配'
}

/** 归属与状态双重门：非归属对账员只读；已收款的单子不能再确认，争议不能重复标记。 */
function actionGate(action: string, row: Row): { disabled: boolean; reason: string } {
  const target = String(row['结算对象'] ?? '')
  const owner = store.ownerOf(target)
  if (!store.isReconciler) {
    return { disabled: true, reason: `当前账号「${store.operator}」不是对账员，仅可查看，不能提交` }
  }
  if (owner !== store.operator) {
    const who = owner ? `归属对账员：${owner}` : '该结算对象尚未分配对账员'
    return { disabled: true, reason: `结算对象「${target}」${who}，当前账号仅可查看` }
  }
  const status = String(row['status'] ?? '')
  if (action === '发起核对' && status !== '待核对') {
    return { disabled: true, reason: `结算单当前为「${status}」，不能再发起核对` }
  }
  if (action === '确认结算' && status === '已收款') {
    return {
      disabled: true,
      reason: `已于 ${row['收款确认时间'] ?? '—'} 由 ${row['收款确认人'] ?? '—'} 确认收款，重复确认只保留首次结果`,
    }
  }
  if (action === '标记争议' && status === '有争议') {
    return {
      disabled: true,
      reason: `争议已由 ${row['争议登记人'] ?? '—'} 于 ${row['争议登记时间'] ?? '—'} 登记，请勿重复标记`,
    }
  }
  return { disabled: false, reason: '' }
}

function runAction(action: string, row: Row) {
  setMessage('info', '')
  if (actionGate(action, row).disabled) {
    setMessage('error', actionGate(action, row).reason)
    return
  }
  if (action === '发起核对') {
    void postAction(row, action, {})
    return
  }
  dialog.action = action
  dialog.row = row
  dialog.open = true
  receiptAmount.value = row['已收金额'] != null ? String(row['已收金额']) : String(row['应收金额'] ?? '')
  disputeReason.value = ''
}

function closeDialog() {
  if (submitting.value) return
  dialog.open = false
  dialog.row = null
}

async function submitDialog() {
  if (!dialog.row) return
  submitting.value = true
  try {
    const extra: Record<string, string> =
      dialog.action === '确认结算'
        ? { 收款金额: receiptAmount.value }
        : { 争议原因: disputeReason.value }
    await postAction(dialog.row, dialog.action, extra)
  } finally {
    submitting.value = false
  }
}

/** 统一动作提交：operator 必带；后端返回 ok=false 时把拒绝原因展示出来，且本地不做乐观改动。 */
async function postAction(row: Row, action: string, extra: Record<string, string>) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, operator: store.operator, ...extra } }),
    })
    let payload: { ok?: boolean; message?: string } = {}
    try {
      payload = await response.json()
    } catch {
      /* 非 JSON 响应走下面的状态码兜底 */
    }
    if (!response.ok || payload.ok === false) {
      setMessage('error', payload.message || `作业结算动作被拒绝（HTTP ${response.status}）`)
      return
    }
    dialog.open = false
    dialog.row = null
    setMessage('info', payload.message || '操作已生效')
    await reload()
  } catch (error) {
    setMessage('error', error instanceof Error ? error.message : '作业结算操作失败')
  }
}

async function openDetail(row: Row) {
  setMessage('info', '')
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      setMessage('error', '结算单详情读取失败')
      return
    }
    detailRow.value = (await response.json()) as Row
  } catch (error) {
    setMessage('error', error instanceof Error ? error.message : '结算单详情读取失败')
  }
}

function formatAmount(value: Cell | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const amount = Number(value)
  return Number.isFinite(amount) ? amount.toFixed(2) : String(value)
}

function formatCell(field: string, value: Cell | undefined): string {
  if (field === '应收金额' || field === '已收金额') return formatAmount(value)
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

async function reload() {
  setMessage('info', '')
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('结算单列表读取失败')
    }
    const payload = (await response.json()) as { items?: Row[]; total?: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    setMessage('error', error instanceof Error ? error.message : '作业结算列表读取失败')
  }
}

onMounted(reload)
</script>

<style scoped>
.scope-hint {
  margin: 0 0 12px;
  padding: 8px 12px;
  border: 1px solid #b9d4ff;
  border-radius: 6px;
  background: #f2f7ff;
  font-size: 12px;
  color: #1f4e9d;
}
.scope-hint.locked {
  border-color: #f3d2a3;
  background: #fff7ed;
  color: #9a5b13;
}
.info-text {
  color: #1f6feb;
}
.link:disabled {
  color: #9aa4b2;
  cursor: not-allowed;
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
  width: 420px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 8px;
  padding: 18px 20px;
}
.modal-card.wide {
  width: 640px;
  max-height: 80vh;
  overflow-y: auto;
}
.modal-title {
  margin: 0 0 6px;
  font-size: 16px;
}
.modal-sub {
  margin: 0 0 14px;
  color: var(--muted);
  font-size: 12px;
}
.modal-line {
  margin: 0 0 10px;
  font-size: 13px;
}
.modal-field {
  display: block;
  margin-bottom: 14px;
}
.modal-field span {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--muted);
}
.modal-field input,
.modal-field textarea {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font: inherit;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 18px;
  margin: 0 0 14px;
}
.detail-grid dt {
  font-size: 12px;
  color: var(--muted);
}
.detail-grid dd {
  margin: 2px 0 0;
  font-size: 13px;
}
</style>

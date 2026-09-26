import { defineStore } from 'pinia'

import { request } from '@/api/client'

/** 接口不可用时也能看到的兜底名册，内容以后端 /api/settle/accounts 为准。 */
const FALLBACK_ACCOUNTS = ['林对账', '周对账', '陈对账']
const FALLBACK_ROSTER: Record<string, string> = {
  远洋海运有限公司: '林对账',
  东港货代公司: '周对账',
  临港冷链物流: '陈对账',
  华景船务代理: '林对账',
}

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '值班管理员',
    shiftLabel: '白班 08:00-20:00',
    scope: '港口集装箱作业调度平台',
    // 对账员账号与「结算对象 → 对账员」归属名册，由后端统一下发
    accounts: [] as string[],
    roster: {} as Record<string, string>,
  }),
  getters: {
    canOperate: (state) => state.operator.length > 0,
    /** 当前账号是否在对账员名册中；非对账员账号对结算单只读。 */
    isReconciler: (state) => state.accounts.includes(state.operator),
    /** 返回某个结算对象对应的负责对账员；未分配归属时返回 undefined。 */
    ownerOf: (state) => (target: string) => state.roster[target],
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
    setOperator(operator: string) {
      this.operator = operator
    },
    /** 拉取对账员账号与归属名册；后端不可用时退回兜底数据，不阻塞页面。 */
    async loadAccounts() {
      try {
        const response = await request('/api/settle/accounts')
        if (!response.ok) {
          throw new Error(`接口返回 ${response.status}`)
        }
        const payload = await response.json()
        this.accounts = Array.isArray(payload.accounts) ? payload.accounts : FALLBACK_ACCOUNTS
        this.roster = payload.roster ?? FALLBACK_ROSTER
      } catch {
        this.accounts = FALLBACK_ACCOUNTS
        this.roster = FALLBACK_ROSTER
      }
    },
  },
})

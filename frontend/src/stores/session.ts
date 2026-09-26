import { defineStore } from 'pinia'

/** 可切换的值班账号：前两位是结算对象归属对账员，值班管理员只有查看权限。 */
export interface OperatorAccount {
  name: string
  role: string
  readonly: boolean
}

export const OPERATOR_ACCOUNTS: OperatorAccount[] = [
  { name: '王敏', role: '结算对账员', readonly: false },
  { name: '李洁', role: '结算对账员', readonly: false },
  { name: '值班管理员', role: '值班管理员（仅查看）', readonly: true },
]

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '王敏',
    role: '结算对账员',
    readonly: false,
    shiftLabel: '白班 08:00-20:00',
    scope: '港口集装箱作业调度平台',
  }),
  getters: {
    canOperate: (state) => state.operator.length > 0,
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
    setAccount(account: OperatorAccount) {
      this.operator = account.name
      this.role = account.role
      this.readonly = account.readonly
    },
  },
})

import { http } from '@/utils/request'

const BASE = '/api/scheduled-publish'

// 定时图集发布 v2 相关 API
export const scheduledPublishApi = {
  // 规则
  listRules() {
    return http.get(`${BASE}/rules`)
  },
  createRule(data) {
    return http.post(`${BASE}/rules`, data)
  },
  updateRule(id, data) {
    return http.put(`${BASE}/rules/${id}`, data)
  },
  deleteRule(id) {
    return http.delete(`${BASE}/rules/${id}`)
  },
  runRule(id) {
    return http.post(`${BASE}/rules/${id}/run`)
  },

  // 运行记录
  listRuns(params = {}) {
    // params: { rule_id, status, days, topic, limit }
    return http.get(`${BASE}/runs`, params)
  },
  getRun(id) {
    return http.get(`${BASE}/runs/${id}`)
  },
  listRunTopics() {
    return http.get(`${BASE}/runs/topics`)
  },
  deleteRun(id) {
    return http.delete(`${BASE}/runs/${id}`)
  },
  clearRuns() {
    return http.delete(`${BASE}/runs`)
  },

  // 预览 / 测试
  preview(ruleId) {
    return http.get(`${BASE}/preview`, { rule_id: ruleId })
  },
  // Cron 表达式 → 下次执行时间（实时预览）
  cronNext(expr) {
    return http.get(`${BASE}/cron/next`, { expr })
  },
  testSmtp() {
    return http.post(`${BASE}/test-smtp`, {})
  },
  testZr(data) {
    return http.post(`${BASE}/test-zr`, data)
  },
}

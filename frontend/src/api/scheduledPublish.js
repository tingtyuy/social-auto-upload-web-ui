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
  listRuns(ruleId, limit) {
    const params = {}
    if (ruleId) params.rule_id = ruleId
    if (limit) params.limit = limit
    return http.get(`${BASE}/runs`, params)
  },
  getRun(id) {
    return http.get(`${BASE}/runs/${id}`)
  },

  // 预览 / 测试
  preview(ruleId) {
    return http.get(`${BASE}/preview`, { rule_id: ruleId })
  },
  testSmtp() {
    return http.post(`${BASE}/test-smtp`, {})
  },
  testZr(data) {
    return http.post(`${BASE}/test-zr`, data)
  },
}

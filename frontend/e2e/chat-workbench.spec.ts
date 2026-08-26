import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/health', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok', service: 'industrial-rag-qa' }) }))
  await page.route('**/v1/knowledge-bases', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: 'kb-1', name: '离心泵运行手册', status: 'ready', document_count: 2, active_document_count: 2, chunk_count: 42 }] }) }))
  await page.route('**/v1/knowledge-bases/kb-1/query', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ request_id: 'e2e-request-1', status: 'success', answer: '结论\n\n请确认入口阀门已打开。', citations: [{ citation_id: 'c-1', document_name: '离心泵运行手册.pdf', page: 12, document_id: 'doc-1', generation_id: 'gen-1', evidence_id: 'e-1', chunk_id: 'hidden' }], claims: [], evidence: [{ evidence_id: 'e-1', citation_id: 'c-1', document_name: '离心泵运行手册.pdf', page: 12, document_id: 'doc-1', generation_id: 'gen-1', chunk_id: 'hidden', excerpt: '确认入口阀门已打开。', relevance_label: '核心依据' }], latency_ms: 25 }) }))
  await page.route('**/v1/knowledge-bases/kb-1/documents/doc-1/source**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ document_id: 'doc-1', document_name: '离心泵运行手册.pdf', knowledge_base_id: 'kb-1', generation_id: 'gen-1', document_version: 2, page: 12, page_context: '启动前确认入口阀门已打开。随后观察压力表。', excerpt: '确认入口阀门已打开。', source_available: true, source_url: '/v1/knowledge-bases/kb-1/documents/doc-1/source-file?page=12' }) }))
  await page.route('**/v1/graph/native**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body>native graph</body></html>' }))
  await page.route('**/v1/graph/overview**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ nodes: [], edges: [], stats: { node_count: 0, edge_count: 0, mode: 'overview', query: null } }) }))
})

test('ordinary operator can ask, inspect evidence, and reach graph without admin navigation', async ({ page }) => {
  await page.goto('/chat')
  await expect(page.getByText('启动与停机')).toBeVisible()
  await expect(page.getByText('管理员入口')).toBeVisible()
  await page.getByRole('button', { name: /离心泵启动前需要检查哪些项目/ }).click()
  await expect(page.getByText('请确认入口阀门已打开。')).toBeVisible()
  await page.getByRole('button', { name: /查看依据/ }).click()
  await expect(page.getByRole('dialog', { name: '证据抽屉' })).toContainText('第 12 页')
  await expect(page.getByRole('dialog', { name: '证据抽屉' })).toContainText('入口阀门已打开')
  await expect(page.getByRole('dialog', { name: '证据抽屉' }).locator('.source-highlight')).toHaveText('确认入口阀门已打开。')
  await expect(page.getByRole('dialog', { name: '证据抽屉' }).getByText('文档版本 2')).toBeVisible()
  await expect(page.getByRole('dialog', { name: '证据抽屉' }).getByRole('link', { name: /打开原始 PDF/ })).toHaveAttribute('href', /page=12/)
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('link', { name: '知识图谱' }).click()
  await expect(page).toHaveURL(/\/graph$/)
  await expect(page.getByText('管理员入口')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Generations' })).toHaveCount(0)
})

test('ordinary operator gets a retryable readiness state when FastAPI is unavailable', async ({ page }) => {
  await page.route('**/health', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ status: 'unavailable' }) }))

  await page.goto('/chat')

  await expect(page.getByText('FastAPI 服务暂不可用。请确认服务已启动，然后重试。').first()).toBeVisible()
  await expect(page.getByRole('textbox', { name: '向当前手册提问' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '重试' })).toBeVisible()
  await expect(page.getByText('API 就绪')).toHaveCount(0)
})

test('ordinary operator gets a clear empty-manual state when no knowledge base is available', async ({ page }) => {
  await page.route('**/v1/knowledge-bases', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) }))

  await page.goto('/chat')

  await expect(page.getByText('当前没有可用手册，请联系管理员上传手册。').first()).toBeVisible()
  await expect(page.getByRole('textbox', { name: '向当前手册提问' })).toBeDisabled()
  await expect(page.getByText('没有可用手册', { exact: true })).toBeVisible()
})

test('graph service failure renders retry state instead of a blank iframe', async ({ page }) => {
  await page.route('**/v1/graph/native**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'GRAPH_UNAVAILABLE', message: '图谱服务暂不可用。' }) }))

  await page.goto('/graph')

  await expect(page.getByText('图谱暂不可用')).toBeVisible()
  await expect(page.getByText('图谱服务暂不可用。')).toBeVisible()
  await expect(page.locator('iframe')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重试' })).toBeVisible()
})

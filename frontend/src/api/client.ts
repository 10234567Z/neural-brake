export type ComponentStatus = { configured: boolean; available: boolean; detail: string }
export type Readiness = { status: string; components: Record<string, ComponentStatus>; httpStatus: number }
export type RuntimeConfig = {
  chain_id: number; pool_address: string; token0_address: string; token1_address: string; fee: number
  pool_selection: string; score_scale: string | null; model_version: string | null; threshold: number | null
  deployments: Record<string, string | null>; publication_enabled: boolean
}
export type Swap = {
  chain_id: number; pool_address: string; block_number: number; block_hash: string
  transaction_hash: string; log_index: number; sender: string; recipient: string
  timestamp: string | null; amount0: string; amount1: string; sqrt_price_x96: string
  liquidity: string; tick: number
}
export type CandidateFeed = { items: unknown[]; available: boolean; reason?: string }
export type ResearchScore = {
  status: 'research_only'; automatic_action_enabled: false; candidate_id: string; model_version: string
  training_dataset_sha256: string; chain_id: number; pool_address: string; as_of: string
  input_age_seconds: number; snapshot_observation_count: number; block_number: number
  transaction_hash: string; log_index: number; risk_score: number; score_scale: 10000
  threshold: number; high_volatility_candidate: boolean; warning: string
}
export type Resource<T> = { value: T | null; error: string | null; stale?: boolean }
export type DashboardData = {
  live: Resource<{ status: string }>; readiness: Resource<Readiness>; config: Resource<RuntimeConfig>
  swaps: Resource<{ items: Swap[]; next_cursor: string | null }>; candidates: Resource<CandidateFeed>
  researchScore: Resource<ResearchScore>; checkedAt: Date
}
const obj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const string = (v: unknown): v is string => typeof v === 'string'
const nullableString = (v: unknown): v is string | null => v === null || string(v)
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v)
const bool = (v: unknown): v is boolean => typeof v === 'boolean'
const address = (v: unknown): v is string => string(v) && /^0x[0-9a-fA-F]{40}$/.test(v)
function component(v: unknown): v is ComponentStatus { return obj(v) && bool(v.configured) && bool(v.available) && string(v.detail) && Object.keys(v).every(k => ['configured','available','detail'].includes(k)) }
function isReadiness(v: unknown): v is Omit<Readiness, 'httpStatus'> {
  return obj(v) && string(v.status) && obj(v.components) && Object.values(v.components).every(component) && Object.keys(v).every(k => ['status','components'].includes(k))
}
function isConfig(v: unknown): v is RuntimeConfig {
  return obj(v) && integer(v.chain_id) && string(v.pool_address) && string(v.token0_address) && string(v.token1_address) &&
    integer(v.fee) && string(v.pool_selection) && nullableString(v.score_scale) && nullableString(v.model_version) &&
    (v.threshold === null || integer(v.threshold)) && obj(v.deployments) && Object.values(v.deployments).every(nullableString) && bool(v.publication_enabled) &&
    address(v.pool_address) && address(v.token0_address) && address(v.token1_address) &&
    Object.keys(v).every(k => ['chain_id','pool_address','token0_address','token1_address','fee','pool_selection','score_scale','model_version','threshold','deployments','publication_enabled'].includes(k)) &&
    Object.keys(v.deployments).every(k => ['score_registry','reactive','vault'].includes(k))
}
function isSwap(v: unknown): v is Swap {
  return obj(v) && integer(v.chain_id) && string(v.pool_address) && integer(v.block_number) && string(v.block_hash) &&
    string(v.transaction_hash) && /^0x[0-9a-fA-F]{64}$/.test(v.transaction_hash) && /^0x[0-9a-fA-F]{64}$/.test(v.block_hash) &&
    address(v.pool_address) && address(v.sender) && address(v.recipient) && integer(v.log_index) && string(v.sender) && string(v.recipient) &&
    (v.timestamp === null || (string(v.timestamp) && Number.isFinite(Date.parse(v.timestamp)))) && string(v.amount0) &&
    string(v.amount1) && string(v.sqrt_price_x96) && string(v.liquidity) && integer(v.tick) && Object.keys(v).every(k =>
      ['chain_id','pool_address','block_number','block_hash','transaction_hash','log_index','sender','recipient','timestamp','amount0','amount1','sqrt_price_x96','liquidity','tick'].includes(k))
}
function isSwapPage(v: unknown): v is { items: Swap[]; next_cursor: string | null } {
  return obj(v) && Array.isArray(v.items) && v.items.every(isSwap) && nullableString(v.next_cursor) && Object.keys(v).every(k => ['items','next_cursor'].includes(k))
}
function isCandidateFeed(v: unknown): v is CandidateFeed {
  return obj(v) && Array.isArray(v.items) && bool(v.available) && (v.reason === undefined || string(v.reason)) && Object.keys(v).every(k => ['items','available','reason'].includes(k))
}
function isResearchScore(v: unknown): v is ResearchScore {
  return obj(v) && v.status === 'research_only' && v.automatic_action_enabled === false &&
    string(v.candidate_id) && string(v.model_version) && string(v.training_dataset_sha256) &&
    integer(v.chain_id) && address(v.pool_address) && string(v.as_of) && Number.isFinite(Date.parse(v.as_of)) &&
    integer(v.input_age_seconds) && v.input_age_seconds >= 0 && integer(v.snapshot_observation_count) &&
    integer(v.block_number) && string(v.transaction_hash) && /^0x[0-9a-fA-F]{64}$/.test(v.transaction_hash) &&
    integer(v.log_index) && integer(v.risk_score) && v.risk_score >= 0 && v.risk_score <= 10000 &&
    v.score_scale === 10000 && integer(v.threshold) && v.threshold >= 0 && v.threshold <= 10000 &&
    bool(v.high_volatility_candidate) && string(v.warning) &&
    Object.keys(v).every(k => ['status','automatic_action_enabled','candidate_id','model_version','training_dataset_sha256','chain_id','pool_address','as_of','input_age_seconds','snapshot_observation_count','block_number','transaction_hash','log_index','risk_score','score_scale','threshold','high_volatility_candidate','warning'].includes(k))
}
async function readJson(path: string): Promise<{ status: number; body: unknown }> {
  const response = await fetch(path, { headers: { Accept: 'application/json' } })
  let body: unknown
  try { body = await response.json() } catch { throw new Error(`API ${response.status} returned invalid JSON`) }
  return { status: response.status, body }
}
async function resource<T>(path: string, validate: (v: unknown) => v is T): Promise<Resource<T>> {
  try {
    const { status, body } = await readJson(path)
    if (!status || status < 200 || status >= 300) throw new Error(`API returned ${status}`)
    if (!validate(body)) throw new Error('API response did not match the expected schema')
    return { value: body, error: null }
  } catch (error) { return { value: null, error: error instanceof Error ? error.message : 'Read failed' } }
}
async function readReadiness(): Promise<Resource<Readiness>> {
  try {
    const { status, body } = await readJson('/api/v1/health/ready')
    const payload = status === 503 && obj(body) ? body.detail : body
    if (!isReadiness(payload)) throw new Error(`Readiness response did not match the expected schema (HTTP ${status})`)
    return { value: { ...payload, httpStatus: status }, error: null }
  } catch (error) { return { value: null, error: error instanceof Error ? error.message : 'Readiness unavailable' } }
}
export async function loadDashboard(): Promise<DashboardData> {
  const [live, readiness, config, swaps, candidates, researchScore] = await Promise.all([
    resource('/api/v1/health/live', (v): v is { status: string } => obj(v) && string(v.status)),
    readReadiness(),
    resource('/api/v1/config', isConfig),
    resource('/api/v1/swaps?limit=20', isSwapPage),
    resource('/api/v1/candidates/latest', isCandidateFeed),
    resource('/api/v1/research/latest-score', isResearchScore),
  ])
  return { live, readiness, config, swaps, candidates, researchScore, checkedAt: new Date() }
}

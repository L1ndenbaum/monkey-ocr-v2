export type OcrMode = 'parse' | 'text' | 'formula' | 'table'

export type JobStatus =
  'queued' | 'processing' | 'extracting' | 'succeeded' | 'failed'

export interface ApiEnvelope<T> {
  internal_code: string
  message: string
  data: T
}

export interface JobFailure {
  code: string
  message: string
}

export interface JobSummary {
  id: string
  mode: OcrMode
  filename: string
  media_type: string
  status: JobStatus
  stage: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  failure: JobFailure | null
}

export interface MarkdownResult {
  name: string
  content: string
}

export interface JobResult {
  id: string
  markdowns: MarkdownResult[]
  json_files: string[]
  public_files: string[]
  source_url: string
  artifact_url: string
}

export interface BackendStatus {
  available: boolean
}

export const TERMINAL_STATUSES = new Set<JobStatus>(['succeeded', 'failed'])

export const MODE_LABELS: Record<OcrMode, string> = {
  parse: '整页解析',
  text: '文字识别',
  formula: '公式识别',
  table: '表格识别',
}

export const STATUS_LABELS: Record<JobStatus, string> = {
  queued: '排队中',
  processing: '识别中',
  extracting: '整理结果',
  succeeded: '已完成',
  failed: '失败',
}

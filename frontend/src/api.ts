import axios, { AxiosError } from 'axios'

import type {
  ApiEnvelope,
  BackendStatus,
  JobResult,
  JobSummary,
  OcrMode,
} from './types'

const appBase = import.meta.env.BASE_URL

const client = axios.create({
  baseURL: `${appBase}api`,
  timeout: 30_000,
  withCredentials: true,
  headers: { Accept: 'application/json' },
})

export async function submitJob(
  file: File,
  mode: OcrMode,
): Promise<JobSummary> {
  const body = new FormData()
  body.append('file', file)
  body.append('mode', mode)
  const response = await client.post<ApiEnvelope<JobSummary>>('/jobs', body, {
    timeout: 60_000,
  })
  return response.data.data
}

export async function listJobs(): Promise<JobSummary[]> {
  const response = await client.get<ApiEnvelope<JobSummary[]>>('/jobs')
  return response.data.data
}

export async function getJob(jobId: string): Promise<JobSummary> {
  const response = await client.get<ApiEnvelope<JobSummary>>(`/jobs/${jobId}`)
  return response.data.data
}

export async function getJobResult(jobId: string): Promise<JobResult> {
  const response = await client.get<ApiEnvelope<JobResult>>(
    `/jobs/${jobId}/result`,
  )
  return response.data.data
}

export async function getBackendStatus(): Promise<BackendStatus> {
  const response = await client.get<ApiEnvelope<BackendStatus>>('/status')
  return response.data.data
}

export async function getResultJson(
  jobId: string,
  path: string,
): Promise<unknown> {
  const response = await axios.get(resultFileUrl(jobId, path), {
    withCredentials: true,
    responseType: 'json',
  })
  return response.data
}

export async function deleteJob(jobId: string): Promise<void> {
  await client.delete(`/jobs/${jobId}`)
}

export function sourceUrl(jobId: string): string {
  return `${appBase}api/jobs/${encodeURIComponent(jobId)}/source`
}

export function artifactUrl(jobId: string): string {
  return `${appBase}api/jobs/${encodeURIComponent(jobId)}/artifact`
}

export function resultFileUrl(jobId: string, path: string): string {
  const encoded = path
    .split('/')
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join('/')
  return `${appBase}api/jobs/${encodeURIComponent(jobId)}/files/${encoded}`
}

export function errorMessage(error: unknown): string {
  if (error instanceof AxiosError) {
    const payload = error.response?.data as
      Partial<ApiEnvelope<unknown>> | undefined
    if (payload?.message) return payload.message
    if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试。'
  }
  if (error instanceof Error && error.message) return error.message
  return '请求失败，请稍后重试。'
}

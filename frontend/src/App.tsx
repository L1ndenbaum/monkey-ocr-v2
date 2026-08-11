import {
  CheckCircledIcon,
  ExclamationTriangleIcon,
  FilePlusIcon,
  ReloadIcon,
} from '@radix-ui/react-icons'
import {
  Badge,
  Box,
  Button,
  Callout,
  Card,
  Flex,
  Heading,
  RadioCards,
  Spinner,
  Text,
} from '@radix-ui/themes'
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import {
  deleteJob,
  errorMessage,
  getBackendStatus,
  getJob,
  getJobResult,
  listJobs,
  submitJob,
} from './api'
import { JobRail } from './components/JobRail'
import {
  MODE_LABELS,
  STATUS_LABELS,
  TERMINAL_STATUSES,
  type JobResult,
  type JobSummary,
  type OcrMode,
} from './types'

const MediaPreview = lazy(() =>
  import('./components/MediaPreview').then((module) => ({
    default: module.MediaPreview,
  })),
)
const ResultViewer = lazy(() =>
  import('./components/ResultViewer').then((module) => ({
    default: module.ResultViewer,
  })),
)

const modes: Array<{ value: OcrMode; description: string }> = [
  {
    value: 'parse',
    description: 'PDF 或图片，输出完整 Markdown、JSON 与图片资源',
  },
  { value: 'text', description: '识别图片或当前 PDF 页内的文字区域' },
  { value: 'formula', description: '识别图片或当前 PDF 页内的公式区域' },
  { value: 'table', description: '识别图片或当前 PDF 页内的表格区域' },
]

export default function App() {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [selection, setSelection] = useState<File | null>(null)
  const [mode, setMode] = useState<OcrMode>('parse')
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loadedResult, setLoadedResult] = useState<{
    jobId: string
    value: JobResult
  } | null>(null)
  const [backendAvailable, setBackendAvailable] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selectedJob = useMemo(
    () => jobs.find((job) => job.id === selectedId) ?? null,
    [jobs, selectedId],
  )
  const result = loadedResult?.jobId === selectedId ? loadedResult.value : null
  const selectedFileIsPdf = Boolean(
    file &&
    (file.type === 'application/pdf' ||
      file.name.toLowerCase().endsWith('.pdf')),
  )
  const waitingForPdfSelection =
    mode !== 'parse' && selectedFileIsPdf && selection === null

  useEffect(() => {
    void listJobs()
      .then((loaded) => {
        setJobs(loaded)
        setSelectedId((current) => current ?? loaded[0]?.id ?? null)
      })
      .catch((caught) => setError(errorMessage(caught)))
    void getBackendStatus()
      .then((status) => setBackendAvailable(status.available))
      .catch(() => setBackendAvailable(false))
  }, [])

  useEffect(() => {
    if (!selectedJob || TERMINAL_STATUSES.has(selectedJob.status)) return
    const timer = window.setInterval(() => {
      void getJob(selectedJob.id)
        .then((updated) => {
          setJobs((current) =>
            current.map((job) => (job.id === updated.id ? updated : job)),
          )
        })
        .catch((caught) => setError(errorMessage(caught)))
    }, 1500)
    return () => window.clearInterval(timer)
  }, [selectedJob])

  useEffect(() => {
    if (selectedJob?.status !== 'succeeded') return
    void getJobResult(selectedJob.id)
      .then((value) => setLoadedResult({ jobId: selectedJob.id, value }))
      .catch((caught) => setError(errorMessage(caught)))
  }, [selectedJob?.id, selectedJob?.status])

  const onSelection = useCallback(
    (selected: File | null) => setSelection(selected),
    [],
  )

  function chooseFile(nextFile: File | undefined) {
    if (!nextFile) return
    setFile(nextFile)
    setSelection(null)
    setLoadedResult(null)
    setError(null)
  }

  async function runOcr() {
    if (!file) {
      setError('请先选择一个 PDF 或图片文件。')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const upload = mode === 'parse' ? file : (selection ?? file)
      const submitted = await submitJob(upload, mode)
      setJobs((current) => [submitted, ...current])
      setSelectedId(submitted.id)
      setLoadedResult(null)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function removeJob(job: JobSummary) {
    try {
      await deleteJob(job.id)
      const remaining = jobs.filter((item) => item.id !== job.id)
      setJobs(remaining)
      if (selectedId === job.id) {
        setSelectedId(remaining[0]?.id ?? null)
        setLoadedResult(null)
      }
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <Flex align="center" justify="between" gap="4" wrap="wrap">
          <div>
            <Text size="1" weight="bold" color="blue" className="eyebrow">
              INTERNAL DOCUMENT INTELLIGENCE
            </Text>
            <Heading size="6">MonkeyOCR 工作台</Heading>
          </div>
          <Badge
            color={backendAvailable ? 'green' : 'red'}
            size="2"
            variant="soft"
          >
            {backendAvailable === null ? (
              <Spinner size="1" />
            ) : backendAvailable ? (
              <CheckCircledIcon />
            ) : (
              <ExclamationTriangleIcon />
            )}
            {backendAvailable === null
              ? '检查推理服务'
              : backendAvailable
                ? '推理服务可用'
                : '推理服务不可用'}
          </Badge>
        </Flex>
      </header>

      <div className="workspace-grid">
        <JobRail
          jobs={jobs}
          selectedId={selectedId}
          onSelect={(job) => setSelectedId(job.id)}
          onDelete={(job) => void removeJob(job)}
        />

        <main className="workspace-main">
          {error ? (
            <Callout.Root color="red" mb="4">
              <Callout.Icon>
                <ExclamationTriangleIcon />
              </Callout.Icon>
              <Callout.Text>{error}</Callout.Text>
            </Callout.Root>
          ) : null}

          <Card size="3" className="control-card">
            <Flex direction="column" gap="4">
              <Flex justify="between" align="center" gap="3" wrap="wrap">
                <Box>
                  <Heading size="4">新建识别任务</Heading>
                  <Text size="2" color="gray">
                    单文件处理；页面刷新后历史不会永久保留。
                  </Text>
                </Box>
                <input
                  ref={inputRef}
                  type="file"
                  accept="application/pdf,image/png,image/jpeg,image/webp,image/gif"
                  hidden
                  onChange={(event) => chooseFile(event.target.files?.[0])}
                />
                <Button
                  variant="soft"
                  onClick={() => inputRef.current?.click()}
                >
                  <FilePlusIcon /> {file ? '更换文件' : '选择文件'}
                </Button>
              </Flex>

              <RadioCards.Root
                value={mode}
                onValueChange={(value) => {
                  setMode(value as OcrMode)
                  setSelection(null)
                }}
                columns={{ initial: '1', sm: '2', lg: '4' }}
              >
                {modes.map((item) => (
                  <RadioCards.Item key={item.value} value={item.value}>
                    <Flex direction="column" gap="1">
                      <Text weight="bold">{MODE_LABELS[item.value]}</Text>
                      <Text size="1" color="gray">
                        {item.description}
                      </Text>
                    </Flex>
                  </RadioCards.Item>
                ))}
              </RadioCards.Root>

              {file ? (
                <Flex direction="column" gap="3">
                  <Flex justify="between" align="center" wrap="wrap" gap="2">
                    <Text size="2" weight="medium">
                      {file.name} · {(file.size / 1024 / 1024).toFixed(2)} MiB
                    </Text>
                    {selection ? (
                      <Badge color="blue">已选择局部区域</Badge>
                    ) : null}
                  </Flex>
                  <div className="preview-surface">
                    <Suspense fallback={<Spinner size="3" />}>
                      <MediaPreview
                        key={`${file.name}-${file.lastModified}-${mode}`}
                        file={file}
                        mode={mode}
                        onSelection={onSelection}
                      />
                    </Suspense>
                  </div>
                  {waitingForPdfSelection ? (
                    <Callout.Root color="amber" size="1">
                      <Callout.Text>
                        请先在当前 PDF 页选择要识别的区域。
                      </Callout.Text>
                    </Callout.Root>
                  ) : null}
                  <Flex justify="end">
                    <Button
                      size="3"
                      disabled={
                        busy ||
                        backendAvailable === false ||
                        waitingForPdfSelection
                      }
                      onClick={runOcr}
                    >
                      {busy ? <Spinner /> : <ReloadIcon />}
                      {busy ? '正在提交' : `开始${MODE_LABELS[mode]}`}
                    </Button>
                  </Flex>
                </Flex>
              ) : (
                <button
                  type="button"
                  className="drop-zone"
                  onClick={() => inputRef.current?.click()}
                >
                  <FilePlusIcon width="28" height="28" />
                  <Text weight="bold">选择 PDF 或图片开始</Text>
                  <Text size="2" color="gray">
                    最大 50 MiB；PDF 最多 50 页
                  </Text>
                </button>
              )}
            </Flex>
          </Card>

          {selectedJob ? (
            <Card size="3" className="job-detail-card">
              <Flex justify="between" align="center" gap="3" wrap="wrap" mb="4">
                <div>
                  <Heading size="4">{selectedJob.filename}</Heading>
                  <Text size="2" color="gray">
                    {MODE_LABELS[selectedJob.mode]} ·{' '}
                    {STATUS_LABELS[selectedJob.status]}
                  </Text>
                </div>
                <Badge
                  color={
                    selectedJob.status === 'succeeded'
                      ? 'green'
                      : selectedJob.status === 'failed'
                        ? 'red'
                        : 'blue'
                  }
                >
                  {STATUS_LABELS[selectedJob.status]}
                </Badge>
              </Flex>
              {selectedJob.status === 'failed' ? (
                <Callout.Root color="red">
                  <Callout.Text>
                    {selectedJob.failure?.message ?? 'OCR 任务执行失败。'}
                  </Callout.Text>
                </Callout.Root>
              ) : selectedJob.status === 'succeeded' && result ? (
                <Suspense fallback={<Spinner size="3" />}>
                  <ResultViewer job={selectedJob} result={result} />
                </Suspense>
              ) : (
                <Flex
                  align="center"
                  justify="center"
                  direction="column"
                  gap="3"
                  py="8"
                >
                  <Spinner size="3" />
                  <Text color="gray">
                    {STATUS_LABELS[selectedJob.status]}，请稍候…
                  </Text>
                </Flex>
              )}
            </Card>
          ) : null}
        </main>
      </div>
    </div>
  )
}

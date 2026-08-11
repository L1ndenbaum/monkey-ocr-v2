import {
  CheckIcon,
  ClipboardCopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
} from '@radix-ui/react-icons'
import {
  Badge,
  Button,
  Callout,
  Flex,
  Select,
  Spinner,
  Tabs,
  Text,
} from '@radix-ui/themes'
import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'

import { artifactUrl, getResultJson, resultFileUrl } from '../api'
import { resolveMarkdownAsset } from '../resultPaths'
import type { JobResult, JobSummary, MarkdownResult } from '../types'

interface ResultViewerProps {
  job: JobSummary
  result: JobResult
}

export function ResultViewer({ job, result }: ResultViewerProps) {
  const [markdownName, setMarkdownName] = useState(
    result.markdowns[0]?.name ?? '',
  )
  const [jsonName, setJsonName] = useState(result.json_files[0] ?? '')
  const [jsonContent, setJsonContent] = useState<string | null>(null)
  const [jsonLoading, setJsonLoading] = useState(false)
  const [copied, setCopied] = useState(false)

  const markdown = useMemo(
    () =>
      result.markdowns.find((item) => item.name === markdownName) ??
      result.markdowns[0],
    [markdownName, result.markdowns],
  )

  async function copyMarkdown() {
    if (!markdown) return
    await navigator.clipboard.writeText(markdown.content)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  async function loadJson(name: string) {
    setJsonName(name)
    setJsonLoading(true)
    try {
      const value = await getResultJson(job.id, name)
      setJsonContent(JSON.stringify(value, null, 2))
    } catch {
      setJsonContent('无法读取 JSON 结果。')
    } finally {
      setJsonLoading(false)
    }
  }

  if (!markdown && result.json_files.length === 0) {
    return (
      <Callout.Root color="amber">
        <Callout.Text>
          任务已完成，但结果包中没有可预览的 Markdown 或 JSON。
        </Callout.Text>
      </Callout.Root>
    )
  }

  return (
    <section className="result-viewer">
      <Flex
        justify="between"
        align="center"
        gap="3"
        wrap="wrap"
        className="result-toolbar"
      >
        <Flex align="center" gap="2">
          <Text weight="bold">识别结果</Text>
          <Badge color="green">只读</Badge>
        </Flex>
        <Flex gap="2" wrap="wrap">
          {markdown ? (
            <Button variant="soft" onClick={copyMarkdown}>
              {copied ? <CheckIcon /> : <ClipboardCopyIcon />}
              {copied ? '已复制' : '复制 Markdown'}
            </Button>
          ) : null}
          <Button asChild>
            <a href={artifactUrl(job.id)} download>
              <DownloadIcon /> 下载完整结果包
            </a>
          </Button>
        </Flex>
      </Flex>

      <Tabs.Root defaultValue="preview">
        <Tabs.List>
          <Tabs.Trigger value="preview">渲染预览</Tabs.Trigger>
          <Tabs.Trigger value="markdown">Markdown</Tabs.Trigger>
          <Tabs.Trigger value="json">JSON</Tabs.Trigger>
          <Tabs.Trigger value="files">文件</Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="preview" className="result-tab">
          <MarkdownSelector
            markdowns={result.markdowns}
            value={markdown?.name ?? ''}
            onChange={setMarkdownName}
          />
          {markdown ? (
            <article className="markdown-body">
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[rehypeRaw, rehypeSanitize, rehypeKatex]}
                components={{
                  img: ({ src, alt }) => {
                    const path = resolveMarkdownAsset(
                      markdown.name,
                      src ?? '',
                      result.public_files,
                    )
                    return path ? (
                      <img
                        src={resultFileUrl(job.id, path)}
                        alt={alt ?? ''}
                        loading="lazy"
                      />
                    ) : null
                  },
                  a: ({ href, children }) => (
                    <span title={href}>{children}</span>
                  ),
                }}
              >
                {markdown.content}
              </ReactMarkdown>
            </article>
          ) : (
            <EmptyResult label="没有 Markdown 结果。" />
          )}
        </Tabs.Content>

        <Tabs.Content value="markdown" className="result-tab">
          <MarkdownSelector
            markdowns={result.markdowns}
            value={markdown?.name ?? ''}
            onChange={setMarkdownName}
          />
          {markdown ? (
            <pre className="source-view">{markdown.content}</pre>
          ) : null}
        </Tabs.Content>

        <Tabs.Content value="json" className="result-tab">
          {result.json_files.length > 0 ? (
            <Flex direction="column" gap="3">
              <Select.Root
                value={jsonName}
                onValueChange={(value) => void loadJson(value)}
              >
                <Select.Trigger placeholder="选择 JSON 文件" />
                <Select.Content>
                  {result.json_files.map((name) => (
                    <Select.Item key={name} value={name}>
                      {name}
                    </Select.Item>
                  ))}
                </Select.Content>
              </Select.Root>
              {jsonLoading ? <Spinner /> : null}
              {jsonContent ? (
                <pre className="source-view">{jsonContent}</pre>
              ) : (
                <Button variant="soft" onClick={() => void loadJson(jsonName)}>
                  加载 JSON
                </Button>
              )}
            </Flex>
          ) : (
            <EmptyResult label="没有 JSON 结果。" />
          )}
        </Tabs.Content>

        <Tabs.Content value="files" className="result-tab">
          <Flex direction="column" gap="2">
            {result.public_files.map((path) => (
              <a
                key={path}
                className="result-file-link"
                href={resultFileUrl(job.id, path)}
                target="_blank"
                rel="noreferrer"
              >
                <span>{path}</span>
                <ExternalLinkIcon />
              </a>
            ))}
          </Flex>
        </Tabs.Content>
      </Tabs.Root>
    </section>
  )
}

function MarkdownSelector({
  markdowns,
  value,
  onChange,
}: {
  markdowns: MarkdownResult[]
  value: string
  onChange: (value: string) => void
}) {
  if (markdowns.length <= 1) return null
  return (
    <Select.Root value={value} onValueChange={onChange}>
      <Select.Trigger mb="3" />
      <Select.Content>
        {markdowns.map((item) => (
          <Select.Item key={item.name} value={item.name}>
            {item.name}
          </Select.Item>
        ))}
      </Select.Content>
    </Select.Root>
  )
}

function EmptyResult({ label }: { label: string }) {
  return (
    <Text color="gray" size="2">
      {label}
    </Text>
  )
}

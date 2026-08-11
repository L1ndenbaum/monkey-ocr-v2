import { Cross2Icon, FileTextIcon } from '@radix-ui/react-icons'
import { Badge, Button, Flex, ScrollArea, Text } from '@radix-ui/themes'

import { MODE_LABELS, STATUS_LABELS, type JobSummary } from '../types'

interface JobRailProps {
  jobs: JobSummary[]
  selectedId: string | null
  onSelect: (job: JobSummary) => void
  onDelete: (job: JobSummary) => void
}

const statusColors = {
  queued: 'gray',
  processing: 'blue',
  extracting: 'indigo',
  succeeded: 'green',
  failed: 'red',
} as const

export function JobRail({
  jobs,
  selectedId,
  onSelect,
  onDelete,
}: JobRailProps) {
  return (
    <aside className="job-rail">
      <Flex justify="between" align="center" px="4" py="3">
        <Text weight="bold">本次会话</Text>
        <Badge variant="soft">{jobs.length}</Badge>
      </Flex>
      <ScrollArea type="auto" scrollbars="vertical" className="job-scroll">
        <Flex direction="column" gap="2" px="2" pb="4">
          {jobs.length === 0 ? (
            <Flex direction="column" align="center" gap="2" py="8">
              <FileTextIcon width="24" height="24" />
              <Text size="2" color="gray">
                暂无任务
              </Text>
            </Flex>
          ) : null}
          {jobs.map((job) => (
            <div
              key={job.id}
              className={`job-item ${selectedId === job.id ? 'job-item-active' : ''}`}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(job)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') onSelect(job)
              }}
            >
              <Flex justify="between" align="start" gap="2">
                <Flex
                  direction="column"
                  align="start"
                  gap="1"
                  className="job-copy"
                >
                  <Text size="2" weight="medium" truncate>
                    {job.filename}
                  </Text>
                  <Text size="1" color="gray">
                    {MODE_LABELS[job.mode]} ·{' '}
                    {new Date(job.created_at).toLocaleTimeString('zh-CN', {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </Text>
                  <Badge
                    color={statusColors[job.status]}
                    size="1"
                    variant="soft"
                  >
                    {STATUS_LABELS[job.status]}
                  </Badge>
                </Flex>
                <Button
                  type="button"
                  size="1"
                  variant="ghost"
                  color="gray"
                  disabled={
                    job.status === 'processing' || job.status === 'extracting'
                  }
                  aria-label={`删除 ${job.filename}`}
                  onClick={(event) => {
                    event.stopPropagation()
                    onDelete(job)
                  }}
                >
                  <Cross2Icon />
                </Button>
              </Flex>
            </div>
          ))}
        </Flex>
      </ScrollArea>
    </aside>
  )
}

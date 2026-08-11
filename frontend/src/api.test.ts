import { describe, expect, it } from 'vitest'

import { artifactUrl, resultFileUrl, sourceUrl } from './api'

describe('protected result URLs', () => {
  it('keeps source and artifact requests below the same-origin BFF', () => {
    expect(sourceUrl('job id')).toBe('/api/jobs/job%20id/source')
    expect(artifactUrl('job id')).toBe('/api/jobs/job%20id/artifact')
  })

  it('encodes each result path segment without flattening directories', () => {
    expect(resultFileUrl('job', 'images/page 1.png')).toBe(
      '/api/jobs/job/files/images/page%201.png',
    )
  })
})

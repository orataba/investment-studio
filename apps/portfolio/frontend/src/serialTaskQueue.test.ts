import { describe, expect, it } from 'vitest'

import { SerialTaskQueue } from '../../../../packages/ui/src/serialTaskQueue'

describe('serial task queue', () => {
  it('does not start a later settings write before the current write finishes', async () => {
    const queue = new SerialTaskQueue()
    const events: string[] = []
    let releaseFirst: (() => void) | undefined

    const first = queue.enqueue(
      () =>
        new Promise<void>((resolve) => {
          events.push('first:start')
          releaseFirst = () => {
            events.push('first:end')
            resolve()
          }
        }),
    )
    const second = queue.enqueue(async () => {
      events.push('second:start')
    })

    await Promise.resolve()
    await Promise.resolve()
    expect(events).toEqual(['first:start'])
    releaseFirst?.()
    await Promise.all([first, second])
    expect(events).toEqual(['first:start', 'first:end', 'second:start'])
  })

  it('continues after a rejected autosave', async () => {
    const queue = new SerialTaskQueue()
    await expect(queue.enqueue(() => Promise.reject(new Error('save failed')))).rejects.toThrow('save failed')
    await expect(queue.enqueue(() => 'next save')).resolves.toBe('next save')
  })

  it('keeps a run save and run creation atomic ahead of later autosaves', async () => {
    const queue = new SerialTaskQueue()
    const events: string[] = []
    let releaseCreate: (() => void) | undefined

    const run = queue.enqueue(async () => {
      events.push('run:save')
      await new Promise<void>((resolve) => {
        events.push('run:create:start')
        releaseCreate = () => {
          events.push('run:create:end')
          resolve()
        }
      })
    })
    const laterAutosave = queue.enqueue(async () => {
      events.push('autosave:later')
    })

    await Promise.resolve()
    await Promise.resolve()
    expect(events).toEqual(['run:save', 'run:create:start'])
    releaseCreate?.()
    await Promise.all([run, laterAutosave])
    expect(events).toEqual(['run:save', 'run:create:start', 'run:create:end', 'autosave:later'])
  })
})

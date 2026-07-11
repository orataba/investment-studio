export class SerialTaskQueue {
  private tail: Promise<void> = Promise.resolve()

  enqueue<T>(task: () => T | Promise<T>): Promise<T> {
    const result = this.tail.catch(() => undefined).then(task)
    this.tail = result.then(
      () => undefined,
      () => undefined,
    )
    return result
  }

  whenIdle() {
    return this.tail
  }
}

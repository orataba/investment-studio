export class ModalStack {
  private readonly tokens: symbol[] = []

  register(token: symbol) {
    this.unregister(token)
    this.tokens.push(token)
  }

  unregister(token: symbol) {
    const index = this.tokens.lastIndexOf(token)
    const wasTopmost = index === this.tokens.length - 1 && index >= 0
    if (index >= 0) {
      this.tokens.splice(index, 1)
    }
    return wasTopmost
  }

  isTopmost(token: symbol) {
    return this.tokens[this.tokens.length - 1] === token
  }

  get size() {
    return this.tokens.length
  }
}

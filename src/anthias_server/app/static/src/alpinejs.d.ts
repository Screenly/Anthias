// Ambient declarations for Alpine.js.
//
// alpinejs ships no type declarations of its own (no "types" field, no
// bundled .d.ts) and there is no maintained @types/alpinejs, so under
// `strict` / `noImplicitAny` a bare `import Alpine from 'alpinejs'`
// resolves to an implicit any. We type just the surface this codebase
// touches — `window.Alpine.store(...)` and the registration helpers —
// so `typeof Alpine` on the Window interface is meaningful. Alpine
// itself is loaded as a global via the vendor bundle, not tree-shaken
// through here.

declare module 'alpinejs' {
  interface Alpine {
    // A store's shape is defined by whoever registers it, so reads come
    // back as `unknown` for the caller to narrow / cast to its own type.
    store(name: string): unknown
    store(name: string, value: unknown): void
    data(name: string, callback: (...args: unknown[]) => unknown): void
    magic(name: string, callback: (...args: unknown[]) => unknown): void
    directive(name: string, callback: (...args: unknown[]) => unknown): void
    start(): void
  }

  const Alpine: Alpine
  export default Alpine
}

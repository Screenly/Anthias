import '@testing-library/jest-dom'

Object.defineProperties(globalThis, {
  WritableStream: {
    value:
      globalThis.WritableStream || require('node:stream/web').WritableStream,
    writable: true,
  },
  ReadableStream: {
    value:
      globalThis.ReadableStream || require('node:stream/web').ReadableStream,
    writable: true,
  },
  TransformStream: {
    value:
      globalThis.TransformStream || require('node:stream/web').TransformStream,
    writable: true,
  },
})

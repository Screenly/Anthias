/* eslint-disable no-console */

import { afterEach, expect, beforeAll } from 'bun:test'
import { cleanup } from '@testing-library/react'
import * as matchers from '@testing-library/jest-dom/matchers'

expect.extend(matchers)

// Suppress console warnings during tests
beforeAll(() => {
  const originalConsoleWarn = console.warn
  const originalConsoleError = console.error

  console.warn = (...args) => {
    const message = args[0]
    if (
      typeof message === 'string' &&
      (message.includes('act(...)') ||
        message.includes('Selector') ||
        message.includes('returned a different result') ||
        message.includes('should be memoized'))
    ) {
      return
    }
    originalConsoleWarn(...args)
  }

  console.error = (...args) => {
    const message = args[0]
    if (
      typeof message === 'string' &&
      (message.includes('act(...)') ||
        message.includes('Selector') ||
        message.includes('returned a different result') ||
        message.includes('should be memoized'))
    ) {
      return
    }
    originalConsoleError(...args)
  }
})

afterEach(() => {
  cleanup()
})

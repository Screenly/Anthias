import { test, expect, beforeAll, afterEach, afterAll } from 'bun:test'
import { render, screen, act } from '@testing-library/react'
import { SystemInfo } from '@/components/system-info'
import { createMockServer } from '@/tests/utils'

const server = createMockServer()

beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

test('SystemInfo renders the system info', async () => {
  await act(async () => {
    render(<SystemInfo />)
  })

  expect(screen.getByRole('heading')).toHaveTextContent('System Info')

  // Verify all system info sections are rendered
  expect(screen.getByText('Load Average')).toBeInTheDocument()
  expect(screen.getByText('Free Space')).toBeInTheDocument()
  expect(screen.getByText('Memory')).toBeInTheDocument()
  expect(screen.getByText('Uptime')).toBeInTheDocument()
  expect(screen.getByText('Display Power (CEC)')).toBeInTheDocument()
  expect(screen.getByText('Device Model')).toBeInTheDocument()
  expect(screen.getByText('Anthias Version')).toBeInTheDocument()
  expect(screen.getByText('MAC Address')).toBeInTheDocument()
})

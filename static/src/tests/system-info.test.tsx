import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'
import { SystemInfo } from '@/components/system-info'

import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'

const server = setupServer(
  http.get('/api/v2/info', () => {
    return HttpResponse.json({
      loadavg: 1.58,
      free_space: '31G',
      display_power: 'CEC error',
      uptime: {
        days: 8,
        hours: 18.56,
      },
      memory: {
        total: 15659,
        used: 9768,
        free: 1522,
        shared: 1439,
        buff: 60,
        available: 3927,
      },
      device_model: 'Generic x86_64 Device',
      anthias_version: 'master@3a4747f',
      mac_address: 'Unable to retrieve MAC address.',
    })
  }),
  http.get('/api/v2/device_settings', () => {
    return HttpResponse.json({
      player_name: 'Test Player',
    })
  }),
)

beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

describe('SystemInfo', () => {
  it('renders the system info', async () => {
    render(<SystemInfo />)

    expect(screen.getByRole('heading')).toHaveTextContent('System Info')

    expect(screen.getByText('Load Average')).toBeInTheDocument()
    expect(screen.getByText('Free Space')).toBeInTheDocument()
    expect(screen.getByText('Memory')).toBeInTheDocument()
    expect(screen.getByText('Uptime')).toBeInTheDocument()
    expect(screen.getByText('Display Power (CEC)')).toBeInTheDocument()
    expect(screen.getByText('Device Model')).toBeInTheDocument()
    expect(screen.getByText('Anthias Version')).toBeInTheDocument()
    expect(screen.getByText('MAC Address')).toBeInTheDocument()

    const expectedValues = [
      '1.58',
      '31G',
      'CEC error',
      '8 days and 18.56 hours',
      'Generic x86_64 Device',
      'master@3a4747f',
      'Unable to retrieve MAC address.',
    ]

    for (const value of expectedValues) {
      const element = await screen.findByText(value)
      expect(element).toBeInTheDocument()
    }
  })
})

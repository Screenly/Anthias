import { test, expect } from 'bun:test'
import { render, screen } from '@testing-library/react'
import { Alert } from '@/components/alert'

test('Alert renders the alert message', () => {
  const testMessage = 'This is a test alert message'
  render(<Alert message={testMessage} />)

  expect(screen.getByText(testMessage)).toBeTruthy()
})

test('Alert renders the close button', () => {
  render(<Alert message="Test message" />)

  const closeButton = screen.getByRole('button')
  expect(closeButton).toBeTruthy()
  expect(closeButton.textContent).toBe('×')
})

test('Alert renders with correct structure', () => {
  render(<Alert message="Test message" />)

  const messageElement = screen.getByText('Test message')
  expect(messageElement).toBeTruthy()
  expect(messageElement.tagName).toBe('SPAN')
})

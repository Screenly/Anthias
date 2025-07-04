import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { Alert } from '@/components/alert';

describe('Alert', () => {
  it('renders the alert message', () => {
    const testMessage = 'This is a test alert message';
    render(<Alert message={testMessage} />);

    expect(screen.getByText(testMessage)).toBeTruthy();
  });

  it('renders the close button', () => {
    render(<Alert message="Test message" />);

    const closeButton = screen.getByRole('button');
    expect(closeButton).toBeTruthy();
    expect(closeButton.textContent).toBe('×');
  });

  it('renders with correct structure', () => {
    render(<Alert message="Test message" />);

    const messageElement = screen.getByText('Test message');
    expect(messageElement).toBeTruthy();
    expect(messageElement.tagName).toBe('SPAN');
  });
});

import { useState } from 'react'
import { render, screen, act, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom'
import { ScheduleFields } from '@/components/edit-asset-modal/schedule-fields'
import { EditFormData } from '@/types'

const baseForm: EditFormData = {
  name: 'Asset',
  start_date: '',
  end_date: '',
  duration: '10',
  mimetype: 'webpage',
  nocache: false,
  skip_asset_check: false,
  play_days: [1, 2, 3, 4, 5, 6, 7],
  play_time_from: null,
  play_time_to: null,
}

function Harness({
  initial = baseForm,
  onState,
}: {
  initial?: EditFormData
  onState?: (state: EditFormData) => void
}) {
  const [formData, setFormData] = useState<EditFormData>(initial)
  if (onState) onState(formData)
  return <ScheduleFields formData={formData} setFormData={setFormData} />
}

describe('ScheduleFields', () => {
  it('toggles a play day off and on again', () => {
    let latest: EditFormData | null = null
    render(<Harness onState={(s) => (latest = s)} />)

    const monCheckbox = screen.getByLabelText('Mon') as HTMLInputElement
    expect(monCheckbox.checked).toBe(true)

    act(() => {
      fireEvent.click(monCheckbox)
    })
    expect(latest!.play_days).toEqual([2, 3, 4, 5, 6, 7])

    act(() => {
      fireEvent.click(monCheckbox)
    })
    expect(latest!.play_days).toEqual([1, 2, 3, 4, 5, 6, 7])
  })

  it('hides time inputs until restrict-toggle is on', () => {
    render(<Harness />)

    expect(screen.queryByLabelText('Play time from')).toBeNull()
    expect(screen.queryByLabelText('Play time to')).toBeNull()

    const toggle = screen.getByLabelText(
      'Restrict time of day',
    ) as HTMLInputElement
    expect(toggle.checked).toBe(false)
    act(() => {
      fireEvent.click(toggle)
    })

    expect(screen.getByLabelText('Play time from')).toBeInTheDocument()
    expect(screen.getByLabelText('Play time to')).toBeInTheDocument()
  })

  it('clears times when the restrict-toggle is turned off', () => {
    let latest: EditFormData | null = null
    render(
      <Harness
        initial={{
          ...baseForm,
          play_time_from: '09:00',
          play_time_to: '17:00',
        }}
        onState={(s) => (latest = s)}
      />,
    )

    const toggle = screen.getByLabelText(
      'Restrict time of day',
    ) as HTMLInputElement
    expect(toggle.checked).toBe(true)
    act(() => {
      fireEvent.click(toggle)
    })

    expect(latest!.play_time_from).toBeNull()
    expect(latest!.play_time_to).toBeNull()
  })

  it('refuses to uncheck the last remaining day', () => {
    let latest: EditFormData | null = null
    render(
      <Harness
        initial={{ ...baseForm, play_days: [1] }}
        onState={(s) => (latest = s)}
      />,
    )

    const monCheckbox = screen.getByLabelText('Mon') as HTMLInputElement
    expect(monCheckbox.disabled).toBe(true)

    act(() => {
      fireEvent.click(monCheckbox)
    })
    expect(latest!.play_days).toEqual([1])
  })

  it('writes time values back to formData', () => {
    let latest: EditFormData | null = null
    render(
      <Harness
        initial={{
          ...baseForm,
          play_time_from: '09:00',
          play_time_to: '17:00',
        }}
        onState={(s) => (latest = s)}
      />,
    )

    const fromInput = screen.getByLabelText(
      'Play time from',
    ) as HTMLInputElement
    act(() => {
      fireEvent.change(fromInput, { target: { value: '08:30' } })
    })
    expect(latest!.play_time_from).toBe('08:30')
  })

  it('preserves HH:MM:SS state when the input is not edited', () => {
    // Assets configured via the v2 API can carry sub-minute precision
    // (e.g. 17:00:01 to include the 17:00 boundary minute). Opening
    // and saving the modal without touching the time inputs must not
    // silently truncate that precision.
    let latest: EditFormData | null = null
    render(
      <Harness
        initial={{
          ...baseForm,
          play_time_from: '09:00:30',
          play_time_to: '17:00:01',
        }}
        onState={(s) => (latest = s)}
      />,
    )

    const fromInput = screen.getByLabelText(
      'Play time from',
    ) as HTMLInputElement
    expect(fromInput.value).toBe('09:00')
    expect(latest!.play_time_from).toBe('09:00:30')
    expect(latest!.play_time_to).toBe('17:00:01')
  })

  it('collapses the whole window when one side is cleared', () => {
    // The v2 API rejects partial windows, so the UI must never let
    // play_time_from and play_time_to drift apart.
    let latest: EditFormData | null = null
    render(
      <Harness
        initial={{
          ...baseForm,
          play_time_from: '09:00',
          play_time_to: '17:00',
        }}
        onState={(s) => (latest = s)}
      />,
    )

    const fromInput = screen.getByLabelText(
      'Play time from',
    ) as HTMLInputElement
    act(() => {
      fireEvent.change(fromInput, { target: { value: '' } })
    })
    expect(latest!.play_time_from).toBeNull()
    expect(latest!.play_time_to).toBeNull()
  })
})

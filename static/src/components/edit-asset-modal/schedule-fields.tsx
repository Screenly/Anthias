import type { Dispatch, SetStateAction } from 'react'

import { EditFormData } from '@/types'

interface ScheduleFieldsProps {
  formData: EditFormData
  setFormData: Dispatch<SetStateAction<EditFormData>>
}

const DAYS: { value: number; label: string }[] = [
  { value: 1, label: 'Mon' },
  { value: 2, label: 'Tue' },
  { value: 3, label: 'Wed' },
  { value: 4, label: 'Thu' },
  { value: 5, label: 'Fri' },
  { value: 6, label: 'Sat' },
  { value: 7, label: 'Sun' },
]

const DEFAULT_TIME_FROM = '09:00'
const DEFAULT_TIME_TO = '17:00'

export const ScheduleFields = ({
  formData,
  setFormData,
}: ScheduleFieldsProps) => {
  const togglePlayDay = (day: number) => {
    setFormData((prev) => {
      const current = prev.play_days
      if (current.includes(day) && current.length === 1) {
        // Refuse to leave play_days empty; the operator should disable
        // the asset rather than schedule it for "no day".
        return prev
      }
      const next = current.includes(day)
        ? current.filter((d) => d !== day)
        : [...current, day].sort((a, b) => a - b)
      return { ...prev, play_days: next }
    })
  }

  const restrictTimeEnabled =
    formData.play_time_from !== null || formData.play_time_to !== null

  const toggleRestrictTime = () => {
    setFormData((prev) => {
      const isRestrictTimeEnabled =
        prev.play_time_from !== null || prev.play_time_to !== null
      return isRestrictTimeEnabled
        ? { ...prev, play_time_from: null, play_time_to: null }
        : {
            ...prev,
            play_time_from: DEFAULT_TIME_FROM,
            play_time_to: DEFAULT_TIME_TO,
          }
    })
  }

  const handleTimeChange = (
    field: 'play_time_from' | 'play_time_to',
    value: string,
  ) => {
    setFormData((prev) => {
      // The v2 API rejects partial windows: clearing one side has to
      // collapse the whole window so we never submit an unsaveable
      // state. The restrict-time toggle reads play_time_from /
      // play_time_to, so this also flips the toggle off.
      if (!value) {
        return { ...prev, play_time_from: null, play_time_to: null }
      }
      return { ...prev, [field]: value }
    })
  }

  return (
    <div className="schedule-fields">
      <div className="row mb-3">
        <label className="col-4 col-form-label">Days of week</label>
        <div className="col-8">
          <div className="d-flex flex-wrap align-items-center">
            {DAYS.map((d) => {
              const isOnlyChecked =
                formData.play_days.length === 1 &&
                formData.play_days[0] === d.value
              return (
                <label
                  key={d.value}
                  className="form-check form-check-inline me-2 mb-1"
                  title={
                    isOnlyChecked
                      ? 'At least one day must be selected'
                      : undefined
                  }
                >
                  <input
                    type="checkbox"
                    className="form-check-input"
                    checked={formData.play_days.includes(d.value)}
                    onChange={() => togglePlayDay(d.value)}
                    disabled={isOnlyChecked}
                    aria-label={d.label}
                  />
                  <span className="form-check-label">{d.label}</span>
                </label>
              )
            })}
          </div>
          <small className="text-muted">
            At least one day must be selected. To stop playback entirely,
            disable the asset.
          </small>
        </div>
      </div>
      <div className="row mb-3">
        <label className="col-4 col-form-label">Time of day</label>
        <div className="col-8">
          <label className="form-check mb-2">
            <input
              type="checkbox"
              className="form-check-input"
              checked={restrictTimeEnabled}
              onChange={toggleRestrictTime}
              aria-label="Restrict time of day"
            />
            <span className="form-check-label">Restrict to a daily window</span>
          </label>
          {restrictTimeEnabled && (
            <div className="d-flex align-items-center">
              <input
                className="form-control time shadow-none"
                type="time"
                value={(formData.play_time_from || '').slice(0, 5)}
                onChange={(e) =>
                  handleTimeChange('play_time_from', e.target.value)
                }
                aria-label="Play time from"
                style={{ marginRight: '5px', maxWidth: '150px' }}
              />
              <span className="me-2">to</span>
              <input
                className="form-control time shadow-none"
                type="time"
                value={(formData.play_time_to || '').slice(0, 5)}
                onChange={(e) =>
                  handleTimeChange('play_time_to', e.target.value)
                }
                aria-label="Play time to"
                style={{ maxWidth: '150px' }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

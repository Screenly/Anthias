import { useEffect, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import Swal from 'sweetalert2'
import { RootState, AppDispatch } from '@/types'

import { SWEETALERT_TIMER } from '@/constants'
import {
  fetchSettings,
  fetchDeviceModel,
  updateSettings,
  updateSetting,
} from '@/store/settings'
import { SystemControls } from '@/components/settings/system-controls'
import { Backup } from '@/components/settings/backup'
import { Authentication } from '@/components/settings/authentication'
import { PlayerName } from '@/components/settings/player-name'
import { DefaultDurations } from '@/components/settings/default-durations'
import { AudioOutput } from '@/components/settings/audio-output'
import { DateFormat } from '@/components/settings/date-format'
import { ToggleableSetting } from '@/components/settings/toggleable-setting'
import { Update } from '@/components/settings/update'
import { AirPlay } from '@/components/settings/airplay'

export const Settings = () => {
  const dispatch = useDispatch<AppDispatch>()
  const { settings, deviceModel, isLoading } = useSelector(
    (state: RootState) => state.settings,
  )
  const [upToDate, setUpToDate] = useState<boolean>(true)
  const [isBalena, setIsBalena] = useState<boolean>(false)

  useEffect(() => {
    dispatch(fetchSettings())
    dispatch(fetchDeviceModel())
  }, [dispatch])

  useEffect(() => {
    fetch('/api/v2/info')
      .then((res) => res.json())
      .then((data) => {
        setUpToDate(data.up_to_date)
      })

    fetch('/api/v2/integrations')
      .then((res) => res.json())
      .then((data) => {
        setIsBalena(data.is_balena)
      })
  }, [])

  useEffect(() => {
    const title = settings.playerName
      ? `${settings.playerName} · Settings`
      : 'Settings'
    document.title = title
  }, [settings.playerName])

  const handleInputChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => {
    const { name, value, type } = e.target
    const checked =
      e.target instanceof HTMLInputElement ? e.target.checked : false
    dispatch(
      updateSetting({
        name: name as keyof RootState['settings']['settings'],
        value: type === 'checkbox' ? checked : value,
      }),
    )
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    try {
      await dispatch(updateSettings(settings)).unwrap()

      await Swal.fire({
        title: 'Success!',
        text: 'Settings were successfully saved.',
        icon: 'success',
        timer: SWEETALERT_TIMER,
        showConfirmButton: false,
        customClass: {
          popup: 'swal2-popup',
          title: 'swal2-title',
          htmlContainer: 'swal2-html-container',
        },
      })

      dispatch(fetchSettings())
    } catch (err) {
      await Swal.fire({
        title: 'Error!',
        text: (err as Error).message || 'Failed to save settings',
        icon: 'error',
        customClass: {
          popup: 'swal2-popup',
          title: 'swal2-title',
          htmlContainer: 'swal2-html-container',
          confirmButton: 'swal2-confirm',
        },
      })
    }
  }

  return (
    <div className="container">
      <div className="row py-2">
        <div className="col-12">
          <h4 className="page-header text-white">
            <b>Settings</b>
          </h4>
        </div>
      </div>

      <div className="row content px-3">
        <div className="col-12 my-3">
          <form onSubmit={handleSubmit} className="row">
            <div className="col-6 d-flex flex-column justify-content-between">
              <PlayerName
                settings={settings}
                handleInputChange={handleInputChange}
              />

              <DefaultDurations
                settings={settings}
                handleInputChange={handleInputChange}
              />

              <AudioOutput
                settings={settings}
                handleInputChange={handleInputChange}
                deviceModel={deviceModel}
              />

              <DateFormat
                settings={settings}
                handleInputChange={handleInputChange}
              />

              <Authentication />
            </div>

            <div className="col-6 d-flex flex-column justify-content-start">
              <ToggleableSetting
                settings={settings}
                handleInputChange={handleInputChange}
                label="Show splash screen"
                name="showSplash"
              />

              <ToggleableSetting
                settings={settings}
                handleInputChange={handleInputChange}
                label="Default assets"
                name="defaultAssets"
              />

              <ToggleableSetting
                settings={settings}
                handleInputChange={handleInputChange}
                label="Shuffle playlist"
                name="shufflePlaylist"
              />

              <ToggleableSetting
                settings={settings}
                handleInputChange={handleInputChange}
                label="Use 24-hour clock"
                name="use24HourClock"
              />

              <ToggleableSetting
                settings={settings}
                handleInputChange={handleInputChange}
                label="Debug logging"
                name="debugLogging"
              />
            </div>

            <div className="col-12">
              <div className="text-end">
                <button
                  className="btn btn-long btn-primary"
                  type="submit"
                  disabled={isLoading}
                >
                  {isLoading ? (
                    <span
                      className="spinner-border spinner-border-sm"
                      role="status"
                      aria-hidden="true"
                    ></span>
                  ) : (
                    'Save Settings'
                  )}
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>

      {!upToDate && !isBalena && <Update />}

      <AirPlay />
      <Backup />
      <SystemControls />
    </div>
  )
}

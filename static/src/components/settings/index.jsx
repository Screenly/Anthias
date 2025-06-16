import { useEffect } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import Swal from 'sweetalert2'

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

export const Settings = () => {
  const dispatch = useDispatch()
  const { settings, deviceModel, isLoading } = useSelector(
    (state) => state.settings,
  )

  useEffect(() => {
    document.title = 'Settings'
    dispatch(fetchSettings())
    dispatch(fetchDeviceModel())
  }, [dispatch])

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    dispatch(
      updateSetting({ name, value: type === 'checkbox' ? checked : value }),
    )
  }

  const handleSubmit = async (e) => {
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
        text: err.message || 'Failed to save settings',
        icon: 'error',
        confirmButtonColor: '#dc3545',
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
            <div className="form-group col-6 d-flex flex-column justify-content-between">
              <div className="form-group">
                <label className="small text-secondary">
                  <small>Player name</small>
                </label>
                <input
                  className="form-control shadow-none"
                  name="playerName"
                  type="text"
                  value={settings.playerName}
                  onChange={handleInputChange}
                />
              </div>

              <div className="row">
                <div className="form-group col-6">
                  <label className="small text-secondary">
                    <small>Default duration (seconds)</small>
                  </label>
                  <input
                    className="form-control shadow-none"
                    name="defaultDuration"
                    type="number"
                    value={settings.defaultDuration}
                    onChange={handleInputChange}
                  />
                </div>
                <div className="form-group col-6">
                  <label className="small text-secondary">
                    <small>Default streaming duration (seconds)</small>
                  </label>
                  <input
                    className="form-control shadow-none"
                    name="defaultStreamingDuration"
                    type="number"
                    value={settings.defaultStreamingDuration}
                    onChange={handleInputChange}
                  />
                </div>
              </div>

              <div className="form-group">
                <label className="small text-secondary">
                  <small>Audio output</small>
                </label>
                <select
                  className="form-control shadow-none"
                  name="audioOutput"
                  value={settings.audioOutput}
                  onChange={handleInputChange}
                >
                  <option value="hdmi">HDMI</option>
                  {!deviceModel.includes('Raspberry Pi 5') && (
                    <option value="local">3.5mm jack</option>
                  )}
                </select>
              </div>

              <div className="form-group">
                <label className="small text-secondary">
                  <small>Date format</small>
                </label>
                <select
                  className="form-control shadow-none"
                  name="dateFormat"
                  value={settings.dateFormat}
                  onChange={handleInputChange}
                >
                  <option value="mm/dd/yyyy">month/day/year</option>
                  <option value="dd/mm/yyyy">day/month/year</option>
                  <option value="yyyy/mm/dd">year/month/day</option>
                  <option value="mm-dd-yyyy">month-day-year</option>
                  <option value="dd-mm-yyyy">day-month-year</option>
                  <option value="yyyy-mm-dd">year-month-day</option>
                  <option value="mm.dd.yyyy">month.day.year</option>
                  <option value="dd.mm.yyyy">day.month.year</option>
                  <option value="yyyy.mm.dd">year.month.day</option>
                </select>
              </div>

              <Authentication />
            </div>

            <div className="form-group col-6 d-flex flex-column justify-content-start">
              <div className="form-inline mt-4">
                <label>Show splash screen</label>
                <div className="ml-auto">
                  <label className="is_enabled-toggle toggle switch-light switch-material small m-0">
                    <input
                      name="showSplash"
                      type="checkbox"
                      checked={settings.showSplash}
                      onChange={handleInputChange}
                    />
                    <span>
                      <span></span>
                      <span></span>
                      <a></a>
                    </span>
                  </label>
                </div>
              </div>

              <div className="form-inline mt-4">
                <label>Default assets</label>
                <div className="ml-auto">
                  <label className="is_enabled-toggle toggle switch-light switch-material small m-0">
                    <input
                      name="defaultAssets"
                      type="checkbox"
                      checked={settings.defaultAssets}
                      onChange={handleInputChange}
                    />
                    <span>
                      <span></span>
                      <span></span>
                      <a></a>
                    </span>
                  </label>
                </div>
              </div>

              <div className="form-inline mt-4">
                <label>Shuffle playlist</label>
                <div className="ml-auto">
                  <label className="is_enabled-toggle toggle switch-light switch-material small m-0">
                    <input
                      name="shufflePlaylist"
                      type="checkbox"
                      checked={settings.shufflePlaylist}
                      onChange={handleInputChange}
                    />
                    <span>
                      <span></span>
                      <span></span>
                      <a></a>
                    </span>
                  </label>
                </div>
              </div>

              <div className="form-inline mt-4">
                <label>Use 24-hour clock</label>
                <div className="ml-auto">
                  <label className="is_enabled-toggle toggle switch-light switch-material small m-0">
                    <input
                      name="use24HourClock"
                      type="checkbox"
                      checked={settings.use24HourClock}
                      onChange={handleInputChange}
                    />
                    <span>
                      <span></span>
                      <span></span>
                      <a></a>
                    </span>
                  </label>
                </div>
              </div>

              <div className="form-inline mt-4">
                <label>Debug logging</label>
                <div className="ml-auto">
                  <label className="is_enabled-toggle toggle switch-light switch-material small m-0">
                    <input
                      name="debugLogging"
                      type="checkbox"
                      checked={settings.debugLogging}
                      onChange={handleInputChange}
                    />
                    <span>
                      <span></span>
                      <span></span>
                      <a></a>
                    </span>
                  </label>
                </div>
              </div>
            </div>

            <div className="form-group col-12">
              <div className="text-right">
                <a className="btn btn-long btn-outline-primary mr-2" href="/">
                  Cancel
                </a>
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

      <Backup />
      <SystemControls />
    </div>
  )
}

import { useEffect } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import Swal from 'sweetalert2'

import { SWEETALERT_TIMER } from '@/constants'
import {
  fetchSettings,
  fetchDeviceModel,
  updateSettings,
  createBackup,
  uploadBackup,
  systemOperation,
  updateSetting,
  resetUploadState,
} from '@/store/settings'

export const Settings = () => {
  const dispatch = useDispatch()
  const {
    settings,
    deviceModel,
    prevAuthBackend,
    isLoading,
    isUploading,
    uploadProgress,
  } = useSelector((state) => state.settings)

  const handleBackup = async () => {
    const backupButton = document.getElementById('btn-backup')
    const originalText = backupButton.textContent
    backupButton.textContent = 'Preparing archive...'
    backupButton.disabled = true
    document.getElementById('btn-upload').disabled = true

    try {
      const result = await dispatch(createBackup()).unwrap()
      if (result) {
        window.location = `/static_with_mime/${result}?mime=application/x-tgz`
      }
    } catch (err) {
      await Swal.fire({
        title: 'Error!',
        text:
          err.message ||
          'The operation failed. Please reload the page and try again.',
        icon: 'error',
        confirmButtonColor: '#dc3545',
        customClass: {
          popup: 'swal2-popup',
          title: 'swal2-title',
          htmlContainer: 'swal2-html-container',
          confirmButton: 'swal2-confirm',
        },
      })
    } finally {
      backupButton.textContent = originalText
      backupButton.disabled = false
      document.getElementById('btn-upload').disabled = false
    }
  }

  const handleUpload = (e) => {
    e.preventDefault()
    const fileInput = document.querySelector('[name="backup_upload"]')
    fileInput.value = '' // Reset the file input
    fileInput.click()
  }

  const handleFileUpload = async (e) => {
    const file = e.target.files[0]
    if (!file) return

    document.getElementById('btn-upload').style.display = 'none'
    document.getElementById('btn-backup').style.display = 'none'
    document.querySelector('.progress').style.display = 'block'

    try {
      const result = await dispatch(uploadBackup(file)).unwrap()

      if (result) {
        await Swal.fire({
          title: 'Success!',
          text:
            typeof result === 'string'
              ? result
              : 'Backup uploaded successfully',
          icon: 'success',
          timer: SWEETALERT_TIMER,
          showConfirmButton: false,
          customClass: {
            popup: 'swal2-popup',
            title: 'swal2-title',
            htmlContainer: 'swal2-html-container',
          },
        })

        // Fetch updated settings after successful recovery
        dispatch(fetchSettings())
      }
    } catch (err) {
      await Swal.fire({
        title: 'Error!',
        text:
          err.message ||
          'The operation failed. Please reload the page and try again.',
        icon: 'error',
        confirmButtonColor: '#dc3545',
        customClass: {
          popup: 'swal2-popup',
          title: 'swal2-title',
          htmlContainer: 'swal2-html-container',
          confirmButton: 'swal2-confirm',
        },
      })
    } finally {
      dispatch(resetUploadState())
      document.querySelector('.progress').style.display = 'none'
      document.getElementById('btn-upload').style.display = 'inline-block'
      document.getElementById('btn-backup').style.display = 'inline-block'
      // Reset the file input
      e.target.value = ''
    }
  }

  const handleSystemOperation = async (operation) => {
    const config = {
      reboot: {
        title: 'Are you sure?',
        text: 'Are you sure you want to reboot your device?',
        confirmButtonText: 'Reboot',
        endpoint: '/api/v2/reboot',
        successMessage: 'Reboot has started successfully.',
        errorMessage: 'Failed to reboot device',
      },
      shutdown: {
        title: 'Are you sure?',
        text: 'Are you sure you want to shutdown your device?',
        confirmButtonText: 'Shutdown',
        endpoint: '/api/v2/shutdown',
        successMessage:
          'Device shutdown has started successfully.\nSoon you will be able to unplug the power from your Raspberry Pi.',
        errorMessage: 'Failed to shutdown device',
      },
    }

    const { title, text, confirmButtonText, endpoint, successMessage } =
      config[operation]

    const result = await Swal.fire({
      title,
      text,
      icon: 'warning',
      showCancelButton: true,
      confirmButtonText,
      cancelButtonText: 'Cancel',
      reverseButtons: true,
      confirmButtonColor: '#dc3545',
      cancelButtonColor: '#6c757d',
      customClass: {
        popup: 'swal2-popup',
        title: 'swal2-title',
        htmlContainer: 'swal2-html-container',
        confirmButton: 'swal2-confirm',
        cancelButton: 'swal2-cancel',
        actions: 'swal2-actions',
      },
    })

    if (result.isConfirmed) {
      try {
        await dispatch(
          systemOperation({ operation, endpoint, successMessage }),
        ).unwrap()

        await Swal.fire({
          title: 'Success!',
          text: successMessage,
          icon: 'success',
          timer: SWEETALERT_TIMER,
          showConfirmButton: false,
          customClass: {
            popup: 'swal2-popup',
            title: 'swal2-title',
            htmlContainer: 'swal2-html-container',
          },
        })
      } catch (err) {
        await Swal.fire({
          title: 'Error!',
          text:
            err.message ||
            'The operation failed. Please reload the page and try again.',
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
  }

  const handleReboot = () => handleSystemOperation('reboot')
  const handleShutdown = () => handleSystemOperation('shutdown')

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

              <div className="form-group mb-0">
                <label className="small text-secondary">
                  <small>Authentication</small>
                </label>
                <select
                  className="form-control shadow-none"
                  id="auth_backend"
                  name="authBackend"
                  value={settings.authBackend}
                  onChange={handleInputChange}
                >
                  <option value="">Disabled</option>
                  <option value="auth_basic">Basic</option>
                </select>
              </div>

              {(settings.authBackend === 'auth_basic' ||
                (settings.authBackend === '' &&
                  prevAuthBackend === 'auth_basic')) && (
                <>
                  {prevAuthBackend === 'auth_basic' && (
                    <div className="form-group" id="curpassword_group">
                      <label className="small text-secondary">
                        <small>Current Password</small>
                      </label>
                      <input
                        className="form-control shadow-none"
                        name="currentPassword"
                        type="password"
                        value={settings.currentPassword}
                        onChange={handleInputChange}
                      />
                    </div>
                  )}
                  {settings.authBackend === 'auth_basic' && (
                    <>
                      <div className="form-group" id="user_group">
                        <label className="small text-secondary">
                          <small>User</small>
                        </label>
                        <input
                          className="form-control shadow-none"
                          name="user"
                          type="text"
                          value={settings.user}
                          onChange={handleInputChange}
                        />
                      </div>
                      <div className="row">
                        <div className="form-group col-6" id="password_group">
                          <label className="small text-secondary">
                            <small>Password</small>
                          </label>
                          <input
                            className="form-control shadow-none"
                            name="password"
                            type="password"
                            value={settings.password}
                            onChange={handleInputChange}
                          />
                        </div>
                        <div className="form-group col-6" id="password2_group">
                          <label className="small text-secondary">
                            <small>Confirm Password</small>
                          </label>
                          <input
                            className="form-control shadow-none"
                            name="confirmPassword"
                            type="password"
                            value={settings.confirmPassword}
                            onChange={handleInputChange}
                          />
                        </div>
                      </div>
                    </>
                  )}
                </>
              )}
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

      {/* Backup Section */}
      <div className="row py-2 mt-4">
        <div className="col-12">
          <h4 className="page-header text-white">
            <b>Backup</b>
          </h4>
        </div>
      </div>
      <div className="row content px-3">
        <div id="backup-section" className="col-12 my-3">
          <div className="text-right">
            <input
              name="backup_upload"
              style={{ display: 'none' }}
              type="file"
              onChange={handleFileUpload}
            />
            <button
              id="btn-backup"
              className="btn btn-long btn-outline-primary mr-2"
              onClick={handleBackup}
              disabled={isUploading}
            >
              Get Backup
            </button>
            <button
              id="btn-upload"
              className="btn btn-primary"
              type="button"
              onClick={handleUpload}
              disabled={isUploading}
            >
              {isUploading ? 'Uploading...' : 'Upload and Recover'}
            </button>
          </div>
          <div
            className="progress-bar progress-bar-striped progress active w-100"
            style={{ display: isUploading ? 'block' : 'none' }}
          >
            <div className="bar" style={{ width: `${uploadProgress}%` }}></div>
          </div>
        </div>
      </div>

      {/* System Controls Section */}
      <div className="row py-2 mt-4">
        <div className="col-12">
          <h4 className="page-header text-white">
            <b>System Controls</b>
          </h4>
        </div>
      </div>
      <div className="row content px-3">
        <div className="col-12 my-3">
          <div className="text-right">
            <button
              className="btn btn-danger btn-long mr-2"
              type="button"
              onClick={handleReboot}
            >
              Reboot
            </button>
            <button
              className="btn btn-danger btn-long"
              type="button"
              onClick={handleShutdown}
            >
              Shutdown
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

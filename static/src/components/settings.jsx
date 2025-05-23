import { useEffect, useState } from 'react'
import { useDispatch } from 'react-redux'
import { fetchDeviceSettings } from '@/store/assets/asset-modal-slice'

export const Settings = () => {
  const dispatch = useDispatch()
  const [settings, setSettings] = useState({
    playerName: '',
    defaultDuration: 0,
    defaultStreamingDuration: 0,
    audioOutput: 'hdmi',
    dateFormat: 'mm/dd/yyyy',
    authBackend: '',
    currentPassword: '',
    user: '',
    password: '',
    confirmPassword: '',
    showSplash: false,
    defaultAssets: false,
    shufflePlaylist: false,
    use24HourClock: false,
    debugLogging: false,
  })

  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [prevAuthBackend, setPrevAuthBackend] = useState('')
  const [isUploading, setIsUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)

  const handleBackup = async () => {
    const backupButton = document.getElementById('btn-backup')
    const originalText = backupButton.textContent
    backupButton.textContent = 'Preparing archive...'
    backupButton.disabled = true
    document.getElementById('btn-upload').disabled = true

    try {
      const response = await fetch('/api/v2/backup', {
        method: 'POST',
      })

      if (!response.ok) {
        throw new Error('Failed to create backup')
      }

      const data = await response.json()
      if (data) {
        window.location = `/static_with_mime/${data}?mime=application/x-tgz`
      }
    } catch (err) {
      setError(
        err.message ||
          'The operation failed. Please reload the page and try again.',
      )
    } finally {
      backupButton.textContent = originalText
      backupButton.disabled = false
      document.getElementById('btn-upload').disabled = false
    }
  }

  const handleUpload = (e) => {
    e.preventDefault()
    document.querySelector('[name="backup_upload"]').click()
  }

  const handleFileUpload = async (e) => {
    const file = e.target.files[0]
    if (!file) return

    setIsUploading(true)
    setUploadProgress(0)
    document.getElementById('btn-upload').style.display = 'none'
    document.getElementById('btn-backup').style.display = 'none'
    document.querySelector('.progress').style.display = 'block'

    const formData = new FormData()
    formData.append('backup_upload', file)

    try {
      const response = await fetch('/api/v2/recover', {
        method: 'POST',
        body: formData,
        onUploadProgress: (progressEvent) => {
          const percentCompleted = Math.round(
            (progressEvent.loaded * 100) / progressEvent.total,
          )
          setUploadProgress(percentCompleted)
          document.querySelector('.progress .bar').style.width =
            `${percentCompleted}%`
          document.querySelector('.progress .bar').textContent =
            `Uploading: ${percentCompleted}%`
        },
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.error || 'Failed to upload backup')
      }

      if (data) {
        setSuccess(
          typeof data === 'string' ? data : 'Backup uploaded successfully',
        )
        setError(null)
      }
    } catch (err) {
      setError(
        err.message ||
          'The operation failed. Please reload the page and try again.',
      )
      setSuccess(null)
    } finally {
      setIsUploading(false)
      document.querySelector('.progress').style.display = 'none'
      document.getElementById('btn-upload').style.display = 'inline-block'
      document.getElementById('btn-backup').style.display = 'inline-block'
    }
  }

  useEffect(() => {
    document.title = 'Settings'
    // Load initial settings
    fetch('/api/v2/device_settings')
      .then((res) => res.json())
      .then((data) => {
        setSettings((prev) => ({
          ...prev,
          playerName: data.player_name || '',
          defaultDuration: data.default_duration || 0,
          defaultStreamingDuration: data.default_streaming_duration || 0,
          audioOutput: data.audio_output || 'hdmi',
          dateFormat: data.date_format || 'mm/dd/yyyy',
          authBackend: data.auth_backend || '',
          user: data.username || '',
          showSplash: data.show_splash || false,
          defaultAssets: data.default_assets || false,
          shufflePlaylist: data.shuffle_playlist || false,
          use24HourClock: data.use_24_hour_clock || false,
          debugLogging: data.debug_logging || false,
        }))
        setPrevAuthBackend(data.auth_backend || '')
      })
      .catch(() => {
        setError('Failed to load settings. Please try again.')
      })
  }, [])

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    if (name === 'authBackend') {
      setPrevAuthBackend(settings.authBackend)
    }
    setSettings((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }))
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setIsLoading(true)
    setError(null)
    setSuccess(null)

    try {
      const response = await fetch('/api/v2/device_settings', {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          player_name: settings.playerName,
          default_duration: settings.defaultDuration,
          default_streaming_duration: settings.defaultStreamingDuration,
          audio_output: settings.audioOutput,
          date_format: settings.dateFormat,
          auth_backend: settings.authBackend,
          current_password: settings.currentPassword,
          username: settings.user,
          password: settings.password,
          password_2: settings.confirmPassword,
          show_splash: settings.showSplash,
          default_assets: settings.defaultAssets,
          shuffle_playlist: settings.shufflePlaylist,
          use_24_hour_clock: settings.use24HourClock,
          debug_logging: settings.debugLogging,
        }),
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.error || 'Failed to save settings')
      }

      setSuccess('Settings were successfully saved.')
      // Clear password after successful save
      setSettings((prev) => ({ ...prev, currentPassword: '' }))
      // Fetch updated device settings
      dispatch(fetchDeviceSettings())
    } catch (err) {
      setError(err.message)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="container">
      {error && (
        <div
          className="alert alert-danger alert-dismissible fade show"
          role="alert"
        >
          {error}
          <button
            type="button"
            className="close"
            onClick={() => setError(null)}
          >
            <span>&times;</span>
          </button>
        </div>
      )}
      {success && (
        <div
          className="alert alert-success alert-dismissible fade show"
          role="alert"
        >
          {success}
          <button
            type="button"
            className="close"
            onClick={() => setSuccess(null)}
          >
            <span>&times;</span>
          </button>
        </div>
      )}

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
                  className="form-control"
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
                    className="form-control"
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
                    className="form-control"
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
                  className="form-control"
                  name="audioOutput"
                  value={settings.audioOutput}
                  onChange={handleInputChange}
                >
                  <option value="hdmi">HDMI</option>
                  <option value="local">3.5mm jack</option>
                </select>
              </div>

              <div className="form-group">
                <label className="small text-secondary">
                  <small>Date format</small>
                </label>
                <select
                  className="form-control"
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
                  className="form-control"
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
                        className="form-control"
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
                          className="form-control"
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
                            className="form-control"
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
                            className="form-control"
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
                  {isLoading ? 'Saving...' : 'Save Settings'}
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
            <button className="btn btn-danger btn-long mr-2" type="button">
              Reboot
            </button>
            <button className="btn btn-danger btn-long" type="button">
              Shutdown
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

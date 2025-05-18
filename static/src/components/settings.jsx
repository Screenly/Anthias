import { useEffect, useState } from 'react'

export const Settings = () => {
  const [settings, setSettings] = useState({
    playerName: '',
    defaultDuration: 0,
    defaultStreamingDuration: 0,
    audioOutput: 'hdmi',
    dateFormat: 'mm/dd/yyyy',
    authBackend: '',
    showSplash: false,
    defaultAssets: false,
    shufflePlaylist: false,
    use24HourClock: false,
    debugLogging: false,
  })

  useEffect(() => {
    document.title = 'Settings'
  }, [])

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    setSettings((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }))
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    // API call will be handled later
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
                <button className="btn btn-long btn-primary" type="submit">
                  Save Settings
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
            />
            <button
              id="btn-backup"
              className="btn btn-long btn-outline-primary mr-2"
            >
              Get Backup
            </button>
            <button id="btn-upload" className="btn btn-primary" type="button">
              Upload and Recover
            </button>
          </div>
          <div
            className="progress-bar progress-bar-striped progress active w-100"
            style={{ display: 'none' }}
          >
            <div className="bar"></div>
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

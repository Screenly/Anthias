import { useDispatch, useSelector } from 'react-redux'
import { RootState } from '@/types'

import { updateSetting } from '@/store/settings'

export const Authentication = () => {
  const dispatch = useDispatch()
  const { settings, prevAuthBackend, hasSavedBasicAuth } = useSelector(
    (state: RootState) => state.settings,
  )

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

  const showCurrentPassword = () => {
    // Show current password if:
    // 1. Current auth is Basic AND hasSavedBasicAuth is true (switching between Basic states)
    // 2. Current auth is Disabled AND hasSavedBasicAuth is true (switching from Basic to Disabled)
    return (
      hasSavedBasicAuth &&
      (settings.authBackend === 'auth_basic' ||
        prevAuthBackend === 'auth_basic')
    )
  }

  return (
    <>
      <div className="mb-3">
        <label className="small text-secondary">
          <small>Authentication</small>
        </label>
        <select
          className="form-control shadow-none form-select"
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
        (settings.authBackend === '' && prevAuthBackend === 'auth_basic')) && (
        <>
          {showCurrentPassword() && (
            <div className="mb-3" id="curpassword_group">
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
              <div className="mb-3" id="user_group">
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
                <div className="col-6 mb-3" id="password_group">
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
                <div className="col-6 mb-3" id="password2_group">
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
    </>
  )
}

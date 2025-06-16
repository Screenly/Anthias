import { useDispatch, useSelector } from 'react-redux'

import { updateSetting } from '@/store/settings'

export const Authentication = () => {
  const dispatch = useDispatch()
  const { settings, prevAuthBackend } = useSelector((state) => state.settings)

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    dispatch(
      updateSetting({ name, value: type === 'checkbox' ? checked : value }),
    )
  }

  return (
    <>
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
        (settings.authBackend === '' && prevAuthBackend === 'auth_basic')) && (
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
    </>
  )
}

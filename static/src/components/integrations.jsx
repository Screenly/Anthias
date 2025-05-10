import { useEffect, useState } from 'react'

export const Integrations = () => {
  const [isBalena] = useState(true) // This will be replaced with actual data later
  const [balenaData] = useState({
    deviceName: 'Device Name', // Placeholder, will be replaced with actual data
    deviceId: 'Device UUID',
    appId: 'App ID',
    appName: 'App Name',
    supervisorVersion: 'Supervisor Version',
    hostOsVersion: 'Host OS Version',
  })

  useEffect(() => {
    document.title = 'Integrations'
  }, [])

  return (
    <div className="container">
      <div className="row py-2">
        <div className="col-12">
          <h4 className="page-header text-white">
            <b>Integrations</b>
          </h4>
        </div>
      </div>
      <div className="row content" style={{ minHeight: '60vh' }}>
        {isBalena && (
          <div id="balena-section" className="col-12">
            <h4 className="page-header">
              <b>Balena</b>
            </h4>
            <table className="table">
              <thead className="table-borderless">
                <tr>
                  <th className="text-secondary font-weight-normal" scope="col">
                    Option
                  </th>
                  <th className="text-secondary font-weight-normal" scope="col">
                    Value
                  </th>
                  <th className="text-secondary font-weight-normal" scope="col">
                    Description
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Device Name</th>
                  <td>{balenaData.deviceName}</td>
                  <td>The name of the device on first initialisation.</td>
                </tr>
                <tr>
                  <th scope="row">Device UUID</th>
                  <td>{balenaData.deviceId}</td>
                  <td>
                    The unique identification number for the device. This is
                    used to identify it on balena.
                  </td>
                </tr>
                <tr>
                  <th scope="row">App ID</th>
                  <td>{balenaData.appId}</td>
                  <td>
                    ID number of the balena application the device is
                    associated.
                  </td>
                </tr>
                <tr>
                  <th scope="row">App Name</th>
                  <td>{balenaData.appName}</td>
                  <td>
                    The name of the balena application the device is associated
                    with.
                  </td>
                </tr>
                <tr>
                  <th scope="row">Supervisor Version</th>
                  <td>{balenaData.supervisorVersion}</td>
                  <td>
                    The current version of the supervisor agent running on the
                    device.
                  </td>
                </tr>
                <tr>
                  <th scope="row">Host OS Version</th>
                  <td>{balenaData.hostOsVersion}</td>
                  <td>The version of the host OS.</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

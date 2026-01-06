import { useEffect, useState } from 'react'

export const Integrations = () => {
  const [data, setData] = useState({
    is_balena: false,
    balena_device_id: '',
    balena_app_id: '',
    balena_app_name: '',
    balena_supervisor_version: '',
    balena_host_os_version: '',
    balena_device_name_at_init: '',
  })
  const [playerName, setPlayerName] = useState('')

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [integrationsResponse, settingsResponse] = await Promise.all([
          fetch('/api/v2/integrations'),
          fetch('/api/v2/device_settings'),
        ])

        const [integrationsData, settingsData] = await Promise.all([
          integrationsResponse.json(),
          settingsResponse.json(),
        ])

        setData(integrationsData)
        setPlayerName(settingsData.player_name ?? '')
      } catch {}
    }

    fetchData()
  }, [])

  useEffect(() => {
    const title = playerName ? `${playerName} · Integrations` : 'Integrations'
    document.title = title
  }, [playerName])

  return (
    <div className="container">
      <div className="row py-3">
        <div className="col-12">
          <h4 className="page-header" style={{ color: '#4C042D', borderBottomColor: '#FEBCC6' }}>
            <b>Integrations</b>
          </h4>
        </div>
      </div>
      <div className="row content" style={{ minHeight: '60vh' }}>
        {data.is_balena && (
          <div id="balena-section" className="col-12">
            <h4 className="page-header" style={{ color: '#4C042D' }}>
              <b>Balena</b>
            </h4>
            <table className="table">
              <thead className="table-borderless">
                <tr>
                  <th className="font-weight-normal" scope="col" style={{ color: '#4C042D' }}>
                    Option
                  </th>
                  <th className="font-weight-normal" scope="col" style={{ color: '#4C042D' }}>
                    Value
                  </th>
                  <th className="font-weight-normal" scope="col" style={{ color: '#4C042D' }}>
                    Description
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.balena_device_name_at_init && (
                  <tr>
                    <th scope="row">Device Name</th>
                    <td>{data.balena_device_name_at_init}</td>
                    <td>The name of the device on first initialisation.</td>
                  </tr>
                )}
                <tr>
                  <th scope="row">Device UUID</th>
                  <td>{data.balena_device_id}</td>
                  <td>
                    The unique identification number for the device. This is
                    used to identify it on balena.
                  </td>
                </tr>
                <tr>
                  <th scope="row">App ID</th>
                  <td>{data.balena_app_id}</td>
                  <td>
                    ID number of the balena application the device is
                    associated.
                  </td>
                </tr>
                <tr>
                  <th scope="row">App Name</th>
                  <td>{data.balena_app_name}</td>
                  <td>
                    The name of the balena application the device is associated
                    with.
                  </td>
                </tr>
                {data.balena_supervisor_version && (
                  <tr>
                    <th scope="row">Supervisor Version</th>
                    <td>{data.balena_supervisor_version}</td>
                    <td>
                      The current version of the supervisor agent running on the
                      device.
                    </td>
                  </tr>
                )}
                <tr>
                  <th scope="row">Host OS Version</th>
                  <td>{data.balena_host_os_version}</td>
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

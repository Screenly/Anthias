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

  useEffect(() => {
    document.title = 'Integrations'
    fetch('/api/v2/integrations')
      .then((response) => response.json())
      .then((data) => {
        setData(data)
      })
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
        {data.is_balena && (
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
                  <td>{data.balena_device_name_at_init}</td>
                  <td>The name of the device on first initialisation.</td>
                </tr>
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

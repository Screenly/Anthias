import { useEffect } from 'react'

export const SystemInfo = () => {
  useEffect(() => {
    document.title = 'System Info'
  }, [])

  return (
    <div className="container">
      <div className="row py-2">
        <div className="col-12">
          <h4 className="page-header text-white">
            <b>System Info</b>
          </h4>
        </div>
      </div>
      <div className="row content">
        <div className="col-12">
          <table className="table mb-5">
            <thead className="table-borderless">
              <tr>
                <th
                  className="text-secondary font-weight-normal"
                  scope="col"
                  style={{ width: '20%' }}
                >
                  Option
                </th>
                <th className="text-secondary font-weight-normal" scope="col">
                  Value
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">Load Average</th>
                <td>0.52, 0.58, 0.59</td>
              </tr>
              <tr>
                <th scope="row">Free Space</th>
                <td>123.45 GB</td>
              </tr>
              <tr>
                <th scope="row">Memory</th>
                <td>
                  Total: <strong>8.0 GB</strong> / Used: <strong>3.2 GB</strong>{' '}
                  / Free: <strong>4.8 GB</strong> / Shared:{' '}
                  <strong>0.0 GB</strong> / Buff: <strong>0.1 GB</strong> /
                  Available: <strong>4.7 GB</strong>
                </td>
              </tr>
              <tr>
                <th scope="row">Uptime</th>
                <td>5 days and 12 hours</td>
              </tr>
              <tr>
                <th scope="row">Display Power (CEC)</th>
                <td>On</td>
              </tr>
              <tr>
                <th scope="row">Device Model</th>
                <td>Raspberry Pi 4 Model B</td>
              </tr>
              <tr>
                <th scope="row">Anthias Version</th>
                <td>
                  <a
                    href="https://github.com/example/anthias/commit/abc123"
                    rel="noopener"
                    target="_blank"
                    className="text-dark"
                  >
                    v1.0.0
                  </a>
                </td>
              </tr>
              <tr>
                <th scope="row">MAC Address</th>
                <td>00:11:22:33:44:55</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

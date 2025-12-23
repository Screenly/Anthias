export const Footer = () => {
  return (
    <footer id="footer" className="bg-dark">
      <div className="container">
        <div className="row">
          <div className="col-6 small text-white mt-5 mb-5">
            <span>
              Powered by Checkin Cast{' '}
              <a
                className="brand"
                href="https://checkin.no/?utm_source=CheckinCast&utm_medium=footer&utm_campaign=UI"
                target="_blank"
                rel="noopener noreferrer"
              >
                <strong>checkin.no</strong>
              </a>
            </span>
          </div>
          <div id="checkin-logo" className="col-12 row m-2 ms-0 me-0">
            <div className="links offset-3 col-6 text-center justify-content-center align-self-center">
              <a
                href="/api/docs/"
                target="_blank"
                className="me-4 small"
                rel="noopener noreferrer"
              >
                API
              </a>
              <a
                href="https://checkin.no/support"
                target="_blank"
                className="me-4 small"
                rel="noopener noreferrer"
              >
                Support
              </a>
              <a
                href="https://checkin.no"
                target="_blank"
                className="me-4 small"
                rel="noopener noreferrer"
              >
                Checkin.no
              </a>
            </div>

            <div
              id="github-stars"
              className="col-3 text-end justify-content-center align-self-center"
            >
            </div>
          </div>
        </div>
      </div>
      <div className="copy pb-4">
        <div className="container">
          <div className="text-center p-2">&copy; Checkin AS</div>
        </div>
      </div>
    </footer>
  )
}

export const Footer = () => {
  return (
    <footer id="footer">
      <div className="container">
        <div className="row">
          <div id="screenly-logo" className="col-12 row m-2 ml-0 mr-0">
            <div className="links offset-3 col-6 text-center justify-content-center align-self-center">
              <a
                href="/api/docs/"
                target="_blank"
                className="mr-4 small"
                rel="noopener noreferrer"
              >
                API
              </a>
              <a
                href="https://anthias.screenly.io/#faq?utm_source=Anthias&utm_medium=footer&utm_campaign=UI"
                target="_blank"
                className="mr-4 small"
                rel="noopener noreferrer"
              >
                FAQ
              </a>
              <a
                href="https://screenly.io/?utm_source=Anthias&utm_medium=footer&utm_campaign=UI"
                target="_blank"
                className="mr-4 small"
                rel="noopener noreferrer"
              >
                Screenly.io
              </a>
              <a
                href="https://forums.screenly.io/"
                target="_blank"
                className="mr-4 small"
                rel="noopener noreferrer"
              >
                Support
              </a>
            </div>

            <div
              id="github-stars"
              className="col-3 text-right justify-content-center align-self-center"
            >
              <a
                href="https://github.com/Screenly/Anthias"
                target="_blank"
                rel="noopener noreferrer"
              >
                <img
                  alt="GitHub Repo stars"
                  src={(() => {
                    const url = new URL(
                      'https://img.shields.io/github/stars/Screenly/Anthias',
                    );
                    const params = new URLSearchParams({
                      style: 'for-the-badge',
                      labelColor: '#EBF0F4',
                      color: '#FFE11A',
                      logo: 'github',
                      logoColor: 'black',
                    });
                    url.search = params.toString();
                    return url.toString();
                  })()}
                />
              </a>
            </div>
          </div>
        </div>
      </div>
      <div className="copy pb-4">
        <div className="container">
          <div className="text-center p-2">&copy; Screenly, Inc.</div>
        </div>
      </div>
    </footer>
  );
};

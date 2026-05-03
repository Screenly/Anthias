export const Footer = () => {
  const starsBadgeUrl = (() => {
    const url = new URL('https://img.shields.io/github/stars/Screenly/Anthias')
    const params = new URLSearchParams({
      style: 'for-the-badge',
      labelColor: '#EBF0F4',
      color: '#FFE11A',
      logo: 'github',
      logoColor: 'black',
    })
    url.search = params.toString()
    return url.toString()
  })()

  return (
    <footer id="footer" className="bg-dark">
      <div className="container py-4">
        <div className="row align-items-center gy-3">
          <div className="col-12 col-lg-4 small text-white">
            Want to get more out of your digital signage?{' '}
            <a
              className="brand"
              href="https://www.screenly.io/?utm_source=Anthias&utm_medium=root-page&utm_campaign=UI"
              target="_blank"
              rel="noopener noreferrer"
            >
              <strong>Try Screenly</strong>
            </a>
          </div>

          <div className="links col-12 col-lg-5 d-flex flex-wrap justify-content-center justify-content-lg-start gap-3">
            <a
              href="/api/docs/"
              target="_blank"
              className="small"
              rel="noopener noreferrer"
            >
              API
            </a>
            <a
              href="https://anthias.screenly.io/#faq?utm_source=Anthias&utm_medium=footer&utm_campaign=UI"
              target="_blank"
              className="small"
              rel="noopener noreferrer"
            >
              FAQ
            </a>
            <a
              href="https://screenly.io/?utm_source=Anthias&utm_medium=footer&utm_campaign=UI"
              target="_blank"
              className="small"
              rel="noopener noreferrer"
            >
              Screenly.io
            </a>
            <a
              href="https://forums.screenly.io/"
              target="_blank"
              className="small"
              rel="noopener noreferrer"
            >
              Support
            </a>
          </div>

          <div
            id="github-stars"
            className="col-12 col-lg-3 d-flex justify-content-center justify-content-lg-end"
          >
            <a
              href="https://github.com/Screenly/Anthias"
              target="_blank"
              rel="noopener noreferrer"
            >
              <img alt="GitHub Repo stars" src={starsBadgeUrl} />
            </a>
          </div>
        </div>
      </div>
      <div className="copy pb-4">
        <div className="container">
          <div className="text-center p-2">&copy; Screenly, Inc.</div>
        </div>
      </div>
    </footer>
  )
}

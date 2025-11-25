import { useEffect } from 'react'
import Tooltip from 'bootstrap/js/dist/tooltip'

export const useTooltipInitialization = (
  activeAssetsCount: number,
  inactiveAssetsCount: number,
) => {
  useEffect(() => {
    const tooltipElements: Tooltip[] = []

    const initializeTooltips = () => {
      tooltipElements.forEach((tooltip) => tooltip.dispose())
      tooltipElements.length = 0

      const tooltipNodes = document.querySelectorAll(
        '[data-bs-toggle="tooltip"]',
      )
      tooltipNodes.forEach((element) => {
        const tooltip = new Tooltip(element as HTMLElement, {
          placement: 'top',
          trigger: 'hover',
          html: true,
          delay: { show: 0, hide: 0 },
          animation: true,
        })
        tooltipElements.push(tooltip)
      })
    }

    initializeTooltips()

    const observer = new MutationObserver(() => {
      initializeTooltips()
    })

    const activeSection = document.getElementById('active-assets-section')
    const inactiveSection = document.getElementById('inactive-assets-section')

    if (activeSection) {
      observer.observe(activeSection, { childList: true, subtree: true })
    }
    if (inactiveSection) {
      observer.observe(inactiveSection, { childList: true, subtree: true })
    }

    return () => {
      observer.disconnect()
      tooltipElements.forEach((tooltip) => tooltip.dispose())
    }
  }, [activeAssetsCount, inactiveAssetsCount])
}

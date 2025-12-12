import { renderHook } from '@testing-library/react'
import { useTooltipInitialization } from './use-tooltip-initialization'

// Mock Bootstrap Tooltip
jest.mock('bootstrap/js/dist/tooltip', () => {
  return jest.fn().mockImplementation(() => ({
    dispose: jest.fn(),
  }))
})

describe('useTooltipInitialization', () => {
  let activeSection: HTMLElement
  let inactiveSection: HTMLElement

  beforeEach(() => {
    // Create mock sections
    activeSection = document.createElement('div')
    activeSection.id = 'active-assets-section'
    document.body.appendChild(activeSection)

    inactiveSection = document.createElement('div')
    inactiveSection.id = 'inactive-assets-section'
    document.body.appendChild(inactiveSection)

    // Mock document.querySelectorAll
    jest
      .spyOn(document, 'querySelectorAll')
      .mockReturnValue([] as unknown as NodeListOf<Element>)
    jest.spyOn(document, 'getElementById').mockImplementation((id) => {
      if (id === 'active-assets-section') return activeSection
      if (id === 'inactive-assets-section') return inactiveSection
      return null
    })
  })

  afterEach(() => {
    activeSection.remove()
    inactiveSection.remove()
    jest.clearAllMocks()
  })

  it('should initialize tooltips on mount', () => {
    renderHook(() => useTooltipInitialization(1, 0))

    expect(document.querySelectorAll).toHaveBeenCalledWith(
      '[data-bs-toggle="tooltip"]',
    )
  })

  it('should set up MutationObserver for both sections', () => {
    const observeSpy = jest.spyOn(MutationObserver.prototype, 'observe')

    renderHook(() => useTooltipInitialization(1, 0))

    expect(observeSpy).toHaveBeenCalledWith(activeSection, {
      childList: true,
      subtree: true,
    })
    expect(observeSpy).toHaveBeenCalledWith(inactiveSection, {
      childList: true,
      subtree: true,
    })
  })

  it('should disconnect observer and dispose tooltips on unmount', () => {
    const disconnectSpy = jest.spyOn(MutationObserver.prototype, 'disconnect')

    const { unmount } = renderHook(() => useTooltipInitialization(1, 0))

    unmount()

    expect(disconnectSpy).toHaveBeenCalled()
  })

  it('should debounce MutationObserver callbacks', async () => {
    jest.useFakeTimers()

    const querySelectorSpy = jest
      .spyOn(document, 'querySelectorAll')
      .mockReturnValue([] as unknown as NodeListOf<Element>)

    // Capture the callback passed to MutationObserver
    let mutationCallback: ((mutations: MutationRecord[]) => void) | null = null
    const mockMutationObserver = jest.fn(
      (callback: (mutations: MutationRecord[]) => void) => {
        mutationCallback = callback
        return {
          observe: jest.fn(),
          disconnect: jest.fn(),
        }
      },
    )
    global.MutationObserver =
      mockMutationObserver as unknown as typeof MutationObserver

    renderHook(() => useTooltipInitialization(1, 0))

    // Initial call on mount
    expect(querySelectorSpy).toHaveBeenCalledTimes(1)
    querySelectorSpy.mockClear()

    // Simulate multiple rapid mutations
    const callback = mutationCallback as unknown as (
      mutations: MutationRecord[],
    ) => void
    callback([])
    callback([])
    callback([])

    // Should not call querySelectorAll yet (debounced)
    expect(querySelectorSpy).not.toHaveBeenCalled()

    // Fast-forward time past debounce delay
    jest.advanceTimersByTime(300)

    // After debounce, it should be called once
    expect(querySelectorSpy).toHaveBeenCalledTimes(1)

    jest.useRealTimers()
  })

  it('should reinitialize tooltips when asset counts change', () => {
    const querySelectorSpy = jest
      .spyOn(document, 'querySelectorAll')
      .mockReturnValue([] as unknown as NodeListOf<Element>)

    const { rerender } = renderHook(
      ({ activeCount, inactiveCount }) =>
        useTooltipInitialization(activeCount, inactiveCount),
      { initialProps: { activeCount: 1, inactiveCount: 0 } },
    )

    querySelectorSpy.mockClear()

    rerender({ activeCount: 2, inactiveCount: 0 })

    expect(querySelectorSpy).toHaveBeenCalled()
  })
})

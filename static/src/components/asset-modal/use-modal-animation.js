import { useState, useEffect, useRef } from 'react'

/**
 * Custom hook for modal animation
 * @param {boolean} isOpen - Whether the modal is open
 * @param {Function} onClose - Callback function to call after closing
 * @returns {Object} - Modal animation state and handlers
 */
export const useModalAnimation = (isOpen, onClose) => {
  const [isVisible, setIsVisible] = useState(false)
  const [isClosing] = useState(false)
  const modalRef = useRef(null)

  // Handle animation when modal opens/closes
  useEffect(() => {
    if (isOpen) {
      // Small delay to ensure the DOM is updated before adding the visible class
      setTimeout(() => {
        setIsVisible(true)
      }, 10)
    } else {
      setIsVisible(false)
    }
  }, [isOpen])

  // Handle clicks outside the modal
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (modalRef.current && !modalRef.current.contains(event.target)) {
        handleClose()
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  /**
   * Handle modal close
   */
  const handleClose = () => {
    // Start the closing animation
    setIsVisible(false)
    // Wait for animation to complete before calling onClose
    setTimeout(() => {
      onClose()
    }, 300)
  }

  return {
    isVisible,
    isClosing,
    modalRef,
    handleClose,
  }
}

import { useState, useEffect, useRef } from 'react';

export const useModalAnimation = (isOpen: boolean, onClose: () => void) => {
  const [isVisible, setIsVisible] = useState(false);
  const [isClosing] = useState(false);
  const modalRef = useRef<HTMLDivElement>(null);

  // Handle animation when modal opens/closes
  useEffect(() => {
    if (isOpen) {
      // Small delay to ensure the DOM is updated before adding the visible class
      setTimeout(() => {
        setIsVisible(true);
      }, 10);
    } else {
      setIsVisible(false);
    }
  }, [isOpen]);

  // Handle clicks outside the modal
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        modalRef.current &&
        !modalRef.current.contains(event.target as Node)
      ) {
        handleClose();
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  /**
   * Handle modal close
   */
  const handleClose = () => {
    // Start the closing animation
    setIsVisible(false);
    // Wait for animation to complete before calling onClose
    setTimeout(() => {
      onClose();
    }, 300);
  };

  return {
    isVisible,
    isClosing,
    modalRef,
    handleClose,
  };
};

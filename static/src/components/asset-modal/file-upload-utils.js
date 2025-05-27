/**
 * Utility functions for file upload handling
 */

/**
 * Get the mimetype based on filename
 * @param {string} filename - The name of the file
 * @returns {string} - The mimetype of the file
 */
export const getMimetype = (filename) => {
  const viduris = ['rtsp', 'rtmp']
  const mimetypes = [
    [['jpe', 'jpg', 'jpeg', 'png', 'pnm', 'gif', 'bmp'], 'image'],
    [['avi', 'mkv', 'mov', 'mpg', 'mpeg', 'mp4', 'ts', 'flv'], 'video'],
  ]
  const domains = [[['www.youtube.com', 'youtu.be'], 'youtube_asset']]

  // Check if it's a streaming URL
  const scheme = filename.split(':')[0].toLowerCase()
  if (viduris.includes(scheme)) {
    return 'streaming'
  }

  // Check if it's a domain-specific asset
  try {
    const domain = filename.split('//')[1].toLowerCase().split('/')[0]
    for (const [domainList, type] of domains) {
      if (domainList.includes(domain)) {
        return type
      }
    }
  } catch (e) {
    // Invalid URL format
  }

  // Check file extension
  try {
    const ext = filename.split('.').pop().toLowerCase()
    for (const [extList, type] of mimetypes) {
      if (extList.includes(ext)) {
        return type
      }
    }
  } catch (e) {
    // No extension found
  }

  // Default to webpage
  return 'webpage'
}

/**
 * Get the duration for a mimetype
 * @param {string} mimetype - The mimetype of the file
 * @param {number} defaultDuration - Default duration for webpage
 * @param {number} defaultStreamingDuration - Default duration for streaming
 * @returns {number} - The duration in seconds
 */
export const getDurationForMimetype = (
  mimetype,
  defaultDuration,
  defaultStreamingDuration,
) => {
  if (mimetype === 'video') {
    return 0
  } else if (mimetype === 'streaming') {
    return defaultStreamingDuration
  } else {
    return defaultDuration
  }
}

/**
 * Get default dates for an asset
 * @returns {Object} - Object containing start_date and end_date
 */
export const getDefaultDates = () => {
  const now = new Date()
  const endDate = new Date()
  endDate.setDate(endDate.getDate() + 30) // 30 days from now

  return {
    start_date: now.toISOString(),
    end_date: endDate.toISOString(),
  }
}

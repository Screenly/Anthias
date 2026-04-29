import { useRef } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import {
  uploadFile,
  saveAsset,
  setErrorMessage,
  setStatusMessage,
  setUploadProgress,
  resetForm,
  selectAssetModalState,
} from '@/store/assets'
import { AppDispatch } from '@/types'

export const useFileUpload = () => {
  const dispatch = useDispatch<AppDispatch>()
  const { formData } = useSelector(selectAssetModalState)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dropZoneRef = useRef<HTMLDivElement>(null)
  // The "Upload completed." message clears 5s after the last file lands.
  // For multi-file uploads, hold the timeout here so a stale clear from
  // an earlier file can't wipe the in-flight "Uploading X of N" label.
  const clearStatusTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  )

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files && files.length > 0) {
      handleFileUploads(Array.from(files))
    }
  }

  const handleFileDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()

    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      handleFileUploads(Array.from(files))
    }
  }

  const handleFileUploads = async (files: File[]) => {
    if (clearStatusTimeoutRef.current) {
      clearTimeout(clearStatusTimeoutRef.current)
      clearStatusTimeoutRef.current = null
    }
    let succeeded = 0
    for (let i = 0; i < files.length; i++) {
      const labelPrefix =
        files.length > 1 ? `Uploading ${i + 1} of ${files.length}: ` : ''
      dispatch(setStatusMessage(`${labelPrefix}${files[i].name}`))
      const ok = await handleFileUpload(files[i])
      if (!ok) {
        return
      }
      succeeded += 1
    }
    if (succeeded > 0) {
      dispatch(
        setStatusMessage(
          succeeded > 1 ? `Uploaded ${succeeded} files.` : 'Upload completed.',
        ),
      )
      clearStatusTimeoutRef.current = setTimeout(() => {
        dispatch(setStatusMessage(''))
        clearStatusTimeoutRef.current = null
      }, 5000)
    }
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleFileUpload = async (file: File): Promise<boolean> => {
    try {
      // Upload the file
      const result = await dispatch(
        uploadFile({ file, skipAssetCheck: formData.skipAssetCheck }),
      ).unwrap()

      // Create asset data
      const assetData = {
        uri: result.fileData.uri,
        ext: result.fileData.ext,
        name: file.name,
        mimetype: result.mimetype,
        is_active: 1,
        is_enabled: 0,
        is_processing: 0,
        nocache: 0,
        play_order: 0,
        duration: result.duration,
        skip_asset_check: formData.skipAssetCheck ? 1 : 0,
        ...result.dates,
      }

      // Save the asset
      await dispatch(saveAsset({ assetData })).unwrap()

      // Reset form for the next file in the batch (the caller in
      // handleFileUploads sets the final "Upload completed." status
      // after the last successful file).
      dispatch(resetForm())

      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      return true
    } catch (error) {
      dispatch(setErrorMessage(`Upload failed: ${(error as Error).message}`))
      dispatch(setUploadProgress(0))

      // Reset the progress bar width directly
      const progressBar = document.querySelector(
        '.progress .bar',
      ) as HTMLElement | null
      if (progressBar) {
        progressBar.style.width = '0%'
      }

      return false
    }
  }

  return {
    fileInputRef,
    dropZoneRef,
    handleFileSelect,
    handleFileDrop,
    handleDragOver,
    handleDragEnter,
    handleDragLeave,
    handleFileUpload,
  }
}

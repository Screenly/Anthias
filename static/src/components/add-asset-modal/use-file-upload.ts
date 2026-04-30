import { useRef } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import {
  uploadFile,
  saveAsset,
  setErrorMessage,
  setStatusMessage,
  setIsSubmitting,
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
    // For batches, the hook drives isSubmitting and statusMessage from
    // start to finish. The per-file thunks run with silent=true so the
    // slice's pending/fulfilled handlers don't toggle isSubmitting between
    // files (which would briefly re-enable the file input/dropzone) or
    // overwrite the "Uploading X of N" label mid-batch.
    dispatch(setIsSubmitting(true))
    let succeeded = 0
    for (let i = 0; i < files.length; i++) {
      const labelPrefix =
        files.length > 1 ? `Uploading ${i + 1} of ${files.length}: ` : ''
      dispatch(setStatusMessage(`${labelPrefix}${files[i].name}`))
      const ok = await handleFileUpload(files[i])
      if (!ok) {
        dispatch(setIsSubmitting(false))
        return
      }
      succeeded += 1
    }
    dispatch(resetForm())
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
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
      // Upload the file. silent=true so the per-file thunks don't toggle
      // statusMessage / isSubmitting — handleFileUploads owns those for
      // the entire batch (single-file uploads still go through this path
      // and the hook sets the same lifecycle around it).
      const result = await dispatch(
        uploadFile({
          file,
          skipAssetCheck: formData.skipAssetCheck,
          silent: true,
        }),
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

      // Save the asset (silent for the same reason as uploadFile).
      await dispatch(saveAsset({ assetData, silent: true })).unwrap()

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

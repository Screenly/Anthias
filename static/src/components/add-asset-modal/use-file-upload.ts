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
    for (let i = 0; i < files.length; i++) {
      const labelPrefix =
        files.length > 1 ? `Uploading ${i + 1} of ${files.length}: ` : ''
      dispatch(setStatusMessage(`${labelPrefix}${files[i].name}`))
      const ok = await handleFileUpload(files[i])
      if (!ok) {
        return
      }
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

      // Reset form and show success message
      dispatch(resetForm())
      dispatch(setStatusMessage('Upload completed.'))

      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      // Hide status message after 5 seconds
      setTimeout(() => {
        dispatch(setStatusMessage(''))
      }, 5000)

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

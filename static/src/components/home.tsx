import { useEffect, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'

import {
  fetchAssets,
  selectActiveAssets,
  selectInactiveAssets,
} from '@/store/assets'
import { AssetEditData, AppDispatch } from '@/types'

import { ActiveAssetsSection } from '@/components/active-assets-section'
import { AddAssetModal } from '@/components/add-asset-modal'
import { EditAssetModal } from '@/components/edit-asset-modal'
import { InactiveAssetsSection } from '@/components/inactive-assets-section'
import { ScheduleHeader } from '@/components/schedule-header'
import { useTooltipInitialization } from '@/hooks/use-tooltip-initialization'

export const ScheduleOverview = () => {
  const dispatch = useDispatch<AppDispatch>()
  const activeAssets = useSelector(selectActiveAssets)
  const inactiveAssets = useSelector(selectInactiveAssets)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [assetToEdit, setAssetToEdit] = useState<AssetEditData | null>(null)
  const [playerName, setPlayerName] = useState('')

  const fetchPlayerName = async () => {
    try {
      const response = await fetch('/api/v2/device_settings')
      const data = await response.json()
      setPlayerName(data.player_name || '')
    } catch {}
  }

  useEffect(() => {
    const title = playerName
      ? `${playerName} · Schedule Overview`
      : 'Schedule Overview'
    document.title = title
    dispatch(fetchAssets())
    fetchPlayerName()
  }, [dispatch, playerName])

  useTooltipInitialization(activeAssets.length, inactiveAssets.length)

  const handleAddAsset = (event: React.MouseEvent) => {
    event.preventDefault()
    setIsModalOpen(true)
    setAssetToEdit(null)
  }

  const handlePreviousAsset = async (event: React.MouseEvent) => {
    event.preventDefault()
    await fetch('/api/v2/assets/control/previous')
  }

  const handleNextAsset = async (event: React.MouseEvent) => {
    event.preventDefault()
    await fetch('/api/v2/assets/control/next')
  }

  const handleCloseModal = () => {
    setIsModalOpen(false)
  }

  const handleSaveAsset = () => {
    setIsModalOpen(false)
  }

  const handleEditAsset = (asset: AssetEditData) => {
    setAssetToEdit(asset)
    setIsEditModalOpen(true)
  }

  const handleCloseEditModal = () => {
    setIsEditModalOpen(false)
    setAssetToEdit(null)
  }

  return (
    <>
      <ScheduleHeader
        playerName={playerName}
        onPreviousAsset={handlePreviousAsset}
        onNextAsset={handleNextAsset}
        onAddAsset={handleAddAsset}
      />

      <span id="assets">
        <ActiveAssetsSection
          activeAssetsCount={activeAssets.length}
          onEditAsset={handleEditAsset}
          onAddAssetClick={handleAddAsset}
        />

        <InactiveAssetsSection
          inactiveAssetsCount={inactiveAssets.length}
          onEditAsset={handleEditAsset}
          onAddAssetClick={handleAddAsset}
        />
      </span>

      <AddAssetModal
        isOpen={isModalOpen}
        onClose={handleCloseModal}
        onSave={handleSaveAsset}
        initialData={assetToEdit || undefined}
      />

      <EditAssetModal
        isOpen={isEditModalOpen}
        onClose={handleCloseEditModal}
        asset={assetToEdit}
      />
    </>
  )
}

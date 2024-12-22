import { AssetRow } from '@/components/asset-row'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { SortableAssetRow } from '@/components/sortable-asset-row'
import { useState, useEffect } from 'react'

export const ActiveAssetsTable = (props) => {
  const [items, setItems] = useState(props.assets);

  useEffect(() => {
    setItems(props.assets);
  }, [props.assets]);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const handleDragEnd = async (event) => {
    const { active, over } = event;

    if (!over || active.id === over.id) {
      return;
    }

    const oldIndex = items.findIndex(asset => asset.asset_id.toString() === active.id);
    const newIndex = items.findIndex(asset => asset.asset_id.toString() === over.id);

    const newItems = arrayMove(items, oldIndex, newIndex);
    setItems(newItems);

    const orderedIds = newItems.map(asset => asset.asset_id).join(',');

    try {
      const response = await fetch('/api/v2/assets/order', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ids: orderedIds })
      });

      if (!response.ok) {
        throw new Error('Failed to update order');
      }

      // Only refresh from server if something else changed
      if (props.onToggle) {
        props.onToggle();
      }
    } catch (error) {
      console.error('Failed to update asset order:', error);
      // Revert to original order on error
      setItems(props.assets);
    }
  };

  return (
    <table className="table">
      <thead className="table-borderless">
        <tr>
          <th className="text-secondary font-weight-normal asset_row_name">Name</th>
          <th className="text-secondary font-weight-normal" style={{ width: '21%' }}>Start</th>
          <th className="text-secondary font-weight-normal" style={{ width: '21%' }}>End</th>
          <th className="text-secondary font-weight-normal" style={{ width: '13%' }}>Duration</th>
          <th className="text-secondary font-weight-normal" style={{ width: '7%' }}>Activity</th>
          <th className="text-secondary font-weight-normal" style={{ width: '13%' }}></th>
        </tr>
      </thead>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <tbody id="active-assets">
          <SortableContext
            items={items.map(a => a.asset_id.toString())}
            strategy={verticalListSortingStrategy}
          >
            {items.map(asset => (
              <SortableAssetRow
                key={asset.asset_id}
                id={asset.asset_id.toString()}
                name={asset.name}
                startDate={asset.start_date}
                endDate={asset.end_date}
                duration={asset.duration}
                isEnabled={asset.is_enabled}
                assetId={asset.asset_id}
                isProcessing={asset.is_processing}
                onToggle={props.onToggle}
              />
            ))}
          </SortableContext>
        </tbody>
      </DndContext>
    </table>
  )
}
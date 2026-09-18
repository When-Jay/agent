import { Drawer } from 'antd';
import type { ReactNode } from 'react';

// 详情抽屉：统一宽度与关闭行为。
export default function DetailDrawer({
  open,
  title,
  width = 640,
  onClose,
  children,
}: {
  open: boolean;
  title: ReactNode;
  width?: number;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Drawer open={open} title={title} width={width} onClose={onClose} destroyOnClose>
      {children}
    </Drawer>
  );
}

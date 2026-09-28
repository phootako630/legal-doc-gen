// 原告信息表内容：名称 / 信用代码 / 法定代表人或负责人 / 住所地
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { BranchEntry } from '@/lib/types';

export function BranchList({ entries }: { entries: BranchEntry[] }) {
  return (
    <Table className="table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead className="w-[30%]">原告名称</TableHead>
          <TableHead className="w-[20%]">统一社会信用代码</TableHead>
          <TableHead className="w-[14%]">法定代表人 / 负责人</TableHead>
          <TableHead>住所地</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entries.map((e) => (
          <TableRow key={e.name}>
            <TableCell className="whitespace-normal font-medium">{e.name}</TableCell>
            <TableCell className="font-mono text-xs">{e.credit_code}</TableCell>
            <TableCell>{e.representative}</TableCell>
            <TableCell className="whitespace-normal text-muted-foreground">{e.address}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

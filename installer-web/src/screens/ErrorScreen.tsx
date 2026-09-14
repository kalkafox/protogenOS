import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export function ErrorScreen({
  message,
  onRestart,
}: {
  message: string
  onRestart: () => void
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-destructive">Installation failed</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="font-mono text-sm break-words">{message}</p>
        <div className="flex justify-end">
          <Button onClick={onRestart}>Back to review</Button>
        </div>
      </CardContent>
    </Card>
  )
}

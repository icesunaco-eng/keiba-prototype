import PDFKit
import Foundation
// Offline: swift pdfkit_names.swift source.pdf name-boxes.json output.json
let args=CommandLine.arguments
guard args.count==4, let document=PDFDocument(url:URL(fileURLWithPath:args[1])) else {fatalError("local PDF and rectangle JSON required")}
let rows=try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:args[2]))) as! [[String:Any]]
var out=[[String:Any]]()
for r in rows {
 guard let p=document.page(at:(r["page"] as! Int)-1) else {fatalError("invalid page")}
 var rr=r
 rr["pdfkit_name"]=p.selection(for:CGRect(x:r["x"] as! Double,y:r["y"] as! Double,width:61,height:7))?.string ?? ""
 out.append(rr)
}
try JSONSerialization.data(withJSONObject:out,options:[.sortedKeys]).write(to:URL(fileURLWithPath:args[3]))

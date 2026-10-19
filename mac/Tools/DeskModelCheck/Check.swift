import AppKit
import SwiftUI

final class DictationMenuFixture: NSObject {
    var calls = 0
    @objc func startDictation(_ item: NSMenuItem) {
        calls += 1
        item.title = calls % 2 == 1 ? "Stop Dictation" : "Start Dictation…"
    }
}

@main
struct DeskModelCheck {
    @MainActor static func main() throws {
        func snapshot(_ rows: [[String:Any]]) throws -> ConversationSnapshot {
            try JSONDecoder().decode(ConversationSnapshot.self, from: JSONSerialization.data(withJSONObject: ["conversations": rows, "keep":3,"look":"auto","rings":true]))
        }
        func row(_ seat:String,_ at:Double,_ unread:Bool,_ activity:Double? = nil) -> [String:Any] {
            ["seat":seat,"latest":"letter-"+seat,"last_at":activity ?? at,"done":false,"unread":unread ? 1:0,
             "messages":[["id":seat,"direction":"in","text":"fixture","at":at,"read": !unread,"via":""]]]
        }
        var checks = 0
        func check(_ value:Bool,_ name:String) {if !value {FileHandle.standardError.write(Data("FAIL \(name)\n".utf8));exit(1)};checks += 1;print("OK \(name)");fflush(stdout)}
        let model=DeskModel();model.seats=["alice","bob","carol"]
        var readBeforeOpen=false
        model.write={_ in readBeforeOpen=true}
        model.receive(try snapshot([row("alice",50,true,200),row("bob",100,true)]))
        check(model.agent == nil && !readBeforeOpen,"background snapshot never opens or marks mail read")
        model.openDesk()
        check(model.agent == "bob","newest unread wins over latest activity")
        model.receive(try snapshot([row("alice",50,false,200),row("bob",100,false)]));model.openDesk()
        check(model.agent == "alice","most recent activity when all are read")
        var hidden=row("carol",300,false);hidden["messages"]=[]
        model.receive(try snapshot([row("alice",50,false),hidden]));model.openDesk()
        check(model.agent == "carol","activity outside Keep is selectable")
        model.chooseAgent(nil);model.receive(try snapshot([row("bob",500,true)]))
        check(model.agent == nil && model.section == .compose,"explicit New letter stays New letter")
        model.receive(try snapshot([]));model.openDesk()
        check(model.agent == nil && model.section == .inbox,"empty Desk is home, not compose")
        model.receive(try snapshot([row("alice",600,true)]))
        check(model.agent == "alice","first snapshot after opening selects conversation")
        var done=row("bob",700,false);done["done"]=true
        var restored=false
        model.write={message in if let action=message["conversation"] as? [String:String],action["action"] == "restore" {restored=true}}
        model.receive(try snapshot([done]));model.openDesk()
        check(model.agent == "bob" && restored,"opening archived latest activity restores it")

        var scroll=ChatScrollState()
        _ = scroll.update(ChatPosition(bottom:400,height:400))
        check(scroll.update(ChatPosition(bottom:400,height:300)) && scroll.atBottom,"shrinking viewport keeps bottom pin")
        check(scroll.update(ChatPosition(bottom:300,height:450)),"growing viewport keeps bottom pin")
        _ = scroll.update(ChatPosition(bottom:900,height:450))
        check(!scroll.update(ChatPosition(bottom:900,height:300)) && !scroll.atBottom,"resize preserves reading position away from bottom")
        _ = scroll.update(ChatPosition(bottom:300,height:300))
        check(!scroll.update(ChatPosition(bottom:310,height:300)) && scroll.atBottom,"normal bottom movement does not trigger resize scroll")
        model.chooseAgent("bob")
        model.editDraft {$0.text="Keep this exact text";$0.recipients=["bob","alice"];$0.picture=PictureDraft(name:"test.png",size:4,payload:["data":"test"])}
        let blocked=Summary(status:"not_sent",reason:"picture_access",request_id:nil,results:[SeatOutcome(to:"bob",status:"not_attempted",id:nil,reason:"picture_access",ring:nil)])
        model.conversationResults[model.draftKey]=blocked
        check(Words.lines(blocked).first?.0 == "Pictures are not set up yet: remove the picture to send the text","picture block explains text-only recovery")
        check(model.canSendWithoutPicture,"wholly blocked picture send offers text-only recovery")
        check(model.pictureSetupSeats == ["bob"],"picture setup action lists only blocked recipient")
        var setupMessages:[[String:Any]]=[]
        model.write={setupMessages.append($0)}
        model.askPictureSetup("outsider");model.askPictureSetup("bob");model.askPictureSetup("bob")
        check(setupMessages.count == 1 && (setupMessages[0]["picture_setup"] as? [String:String])?["seat"] == "bob","setup request targets one blocked seat and ignores duplicate click")
        model.receive(ResultMessage(kind:"picture_setup",ref:"bob",summary:Summary(status:"sent",reason:nil,request_id:nil,results:nil)))
        check(model.pictureSetupStatus["bob"] == "Request sent. Waiting for the agent to read it." && model.draft.picture != nil,"setup result preserves picture draft while waiting for proof")
        let readySnapshot=try JSONDecoder().decode(ConversationSnapshot.self,from:JSONSerialization.data(withJSONObject:["conversations":[],"keep":3,"look":"auto","rings":true,"picture_readers":["bob"]]))
        model.receive(readySnapshot)
        check(model.pictureSetupSeats.isEmpty && model.pictureSetupStatus["bob"] == "Pictures ready. You can send your draft." && model.draft.picture != nil,"verified snapshot removes setup action and preserves unsent picture")
        var packet:[String:Any]=[:]
        model.write={packet=$0["conversation_send"] as? [String:Any] ?? [:]}
        model.sendWithoutPicture()
        check(packet["picture"] == nil && packet["text"] as? String == "Keep this exact text" && packet["to"] as? [String] == ["bob","alice"] && packet["agent"] as? String == "bob","text-only recovery preserves text, recipients and conversation")
        packet=[:];model.sendWithoutPicture()
        check(packet.isEmpty,"text-only action cannot submit twice while sending")
        model.conversationSending=nil
        model.editDraft {$0.picture=PictureDraft(name:"test.png",size:4,payload:[:])}
        model.conversationResults[model.draftKey]=Summary(status:"partial",reason:"picture_access",request_id:"published",results:[])
        check(!model.canSendWithoutPicture,"partial publication cannot resend confirmed copies through text-only action")
        model.conversationResults[model.draftKey]=blocked
        model.editDraft {$0.text="  "}
        check(!model.canSendWithoutPicture,"picture-only draft cannot send empty text")

        let scratch=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at:scratch,withIntermediateDirectories:true)
        defer {try? FileManager.default.removeItem(at:scratch)}
        let real=scratch.appendingPathComponent("real")
        try FileManager.default.createDirectory(at:real,withIntermediateDirectories:true)
        let linked=scratch.appendingPathComponent("linked")
        try FileManager.default.createSymbolicLink(at:linked,withDestinationURL:real)
        let source=real.appendingPathComponent("screenshot.png")
        let bytes=Data([137,80,78,71,13,10,26,10])
        try bytes.write(to:source)
        let captured=try DraftPicture.read(linked.appendingPathComponent("screenshot.png"))
        check(captured == bytes,"picture capture permits symlinked parent directories")
        let symlink=scratch.appendingPathComponent("symlink.png")
        try FileManager.default.createSymbolicLink(at:symlink,withDestinationURL:source)
        var refused=false
        do {_ = try DraftPicture.read(symlink)} catch {refused=true}
        check(refused,"picture capture refuses a symlink at the selected file")
        try FileManager.default.removeItem(at:source)
        model.editDraft {$0.text="Screenshot";$0.picture=PictureDraft(name:"screenshot.png",size:captured.count,payload:["data":captured.base64EncodedString()])}
        model.sendConversation()
        let sentPicture=packet["picture"] as? [String:String]
        check(sentPicture?["data"] == bytes.base64EncodedString() && sentPicture?["path"] == nil,"captured temporary picture remains in send packet after source deletion")

        let app=NSApplication.shared;app.setActivationPolicy(.accessory)
        let window=NSWindow(contentRect:NSRect(x:0,y:0,width:250,height:150),styleMask:[.titled],backing:.buffered,defer:false)
        window.isReleasedWhenClosed=false
        let view=DraftTextView(frame:window.contentView!.bounds);window.contentView=view
        check(!view.responds(to:Selector(("startDictation:"))),"real editor does not implement the Dictation selector")
        let target=EditorTarget();target.textView=view
        var dispatches=0;var focused=false
        // Intercept dispatch instead of adding a selector the real editor does not implement.
        // This test never starts the OS speech engine; live bundle validation covers that.
        target.mainMenu={nil}
        target.sendDictation={dispatches += 1;focused=window.firstResponder === view;return false}
        target.dictate()
        RunLoop.current.run(until:Date().addingTimeInterval(0.15))
        check(dispatches == 1 && focused,"Dictation dispatch waits for the real editor to become first responder")
        check(target.hint == "Use the Dictation shortcut","refused action reports shortcut hint")
        let detached=EditorTarget();detached.dictate()
        check(detached.hint == "Use the Dictation shortcut","missing editor reports shortcut hint")
        let menu=NSMenu();let edit=NSMenu(title:"Edit");let parent=NSMenuItem(title:"Edit",action:nil,keyEquivalent:"")
        parent.submenu=edit;menu.addItem(parent)
        let fixture=DictationMenuFixture()
        let item=NSMenuItem(title:"Start Dictation…",action:#selector(DictationMenuFixture.startDictation(_:)),keyEquivalent:"")
        item.target=fixture;edit.addItem(item)
        target.mainMenu={menu}
        target.dictate();RunLoop.current.run(until:Date().addingTimeInterval(0.15))
        check(fixture.calls == 1 && target.dictating && dispatches == 1,"mic invokes nested menu item and reflects active title without fallback")
        target.dictate();RunLoop.current.run(until:Date().addingTimeInterval(0.15))
        check(fixture.calls == 2 && !target.dictating,"second mic press invokes menu stop operation")
        edit.autoenablesItems=false;item.isEnabled=false
        target.dictate();RunLoop.current.run(until:Date().addingTimeInterval(0.15))
        check(fixture.calls == 2 && target.hint == "Use the Dictation shortcut","disabled menu action is not invoked")
        item.isEnabled=true
        item.title="Stop Dictation…";target.refreshDictation()
        check(target.dictating,"external Dictation menu state updates active indicator")
        item.title="Start Dictation…";item.action=Selector(("stopDictation:"));target.refreshDictation()
        check(target.dictating,"stop action also marks Dictation active")
        view.string=""
        check(view.showsPlaceholder,"empty native editor shows placeholder")
        view.setMarkedText("provisional", selectedRange:NSRange(location:11,length:0),replacementRange:NSRange(location:NSNotFound,length:0))
        check(view.hasMarkedText(),"real editor holds provisional marked text")
        check(!view.showsPlaceholder,"provisional Dictation hides native placeholder even with empty model")
        NativeDraftEditor.applyText("stale model",to:view)
        check(view.string == "provisional","model synchronization preserves Dictation provisional text")
        view.unmarkText();NativeDraftEditor.applyText("committed",to:view)
        check(view.string == "committed","unmarked editor still accepts model changes")
        check(!view.showsPlaceholder,"committed text hides native placeholder")
        view.string="";check(view.showsPlaceholder,"clearing editor restores native placeholder")
        let pasteboard=NSPasteboard.withUniqueName()
        pasteboard.setData(Data([137,80,78,71]),forType:.png)
        let editor=DraftTextView();var picture=false
        editor.picture={data,url in picture = data != nil && url == nil}
        check(editor.takePicture(pasteboard) && picture,"native image paste routes to picture chip")
        pasteboard.clearContents();pasteboard.setString("normal text",forType:.string)
        check(!editor.takePicture(pasteboard),"normal text keeps standard paste behavior")
        window.close();pasteboard.releaseGlobally()
        print("\(checks) checks passed")
    }
}

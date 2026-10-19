import SwiftUI

/// Reached by a long-press on the header.
struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var draftHost = ""
    @State private var confirmForget = false

    var body: some View {
        Group {
            if #available(iOS 16.0, *) {
                NavigationStack { form }
            } else {
                NavigationView { form }
                    .navigationViewStyle(.stack)
            }
        }
        .onAppear {
            draftHost = model.hostOverride
            model.refreshSpeechSupport()
        }
        .preferredColorScheme(.dark)
    }

    private var form: some View {
        Form {
            Section {
                Picker("Ring for", selection: $model.ringPolicy.ringFor) {
                    Text("Everything").tag(RingPolicy.RingFor.everything)
                    Text("Only letters to me").tag(RingPolicy.RingFor.repliesOnly)
                }
                .pickerStyle(.inline)
                .labelsHidden()
            } header: {
                Text("Ring for")
            }

            Section {
                Toggle("Quiet hours", isOn: $model.ringPolicy.quietHours)
                if model.ringPolicy.quietHours {
                    DatePicker("From", selection: time(\.quietFrom), displayedComponents: .hourAndMinute)
                    DatePicker("To", selection: time(\.quietTo), displayedComponents: .hourAndMinute)
                }
            } footer: {
                Text("Mail that doesn't ring still shows on the screen, silently.")
            }

            if model.onDeviceSpeechSupported {
                Section {
                    Picker("Recognise speech", selection: $model.speechOnline) {
                        Text("On this phone (private)").tag(false)
                        Text("Apple's online service (usually more accurate)").tag(true)
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                } header: {
                    Text("Recognise speech")
                } footer: {
                    Text("Online, your voice goes to Apple while you hold the button. Nothing reaches the Mac until you send.")
                }
            } else {
                Section {
                    InfoRow(title: "Speech recognition", value: "Apple's online service")
                } footer: {
                    Text("This phone can't recognise speech on its own, so your voice goes to Apple while you hold the button.")
                }
            }

            Section {
                InfoRow(title: "Found by Bonjour", value: model.discoveredHost ?? "Nothing yet")
                if let host = model.record?.host {
                    InfoRow(title: "Paired at", value: host)
                }
                TextField("desk-mac.local:8765", text: $draftHost)
                    .font(.mono(15))
                    .textInputAutocapitalization(.never)
                    .disableAutocorrection(true)
                    .keyboardType(.URL)
                Button("Use this host") { model.setHostOverride(draftHost) }
                    .disabled(draftHost.trimmingCharacters(in: .whitespaces).isEmpty
                              || draftHost.trimmingCharacters(in: .whitespaces) == model.hostOverride)
                if !model.hostOverride.isEmpty {
                    Button("Use the Mac found by Bonjour") {
                        draftHost = ""
                        model.setHostOverride("")
                    }
                }
            } header: {
                Text("Mac host")
            } footer: {
                Text(model.hostOverride.isEmpty
                     ? "Using the Mac found on this network."
                     : "Using \(ListenerClient.normalizeHost(model.hostOverride)).")
            }

            if model.record != nil {
                Section {
                    Button("Forget this Mac", role: .destructive) { confirmForget = true }
                } footer: {
                    Text("Deletes the pairing key from this phone. You'll pair again with a new code.")
                }
            }

            #if DEBUG
            Section {
                if #available(iOS 16.0, *) {
                    TextField("Type instead of speak", text: $model.typedInsteadOfSpoken, axis: .vertical)
                } else {
                    TextField("Type instead of speak", text: $model.typedInsteadOfSpoken)
                }
            } header: {
                Text("Debug")
            } footer: {
                Text("While this has text, holding the button uses it instead of the microphone.")
            }
            #endif
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Done") { dismiss() }
            }
        }
        .confirmationDialog("Forget this Mac?", isPresented: $confirmForget, titleVisibility: .visible) {
            Button("Forget this Mac", role: .destructive) {
                model.forgetMac()
                dismiss()
            }
        } message: {
            Text("The phone stops ringing for \(model.macName) until you pair again.")
        }
    }

    /// A clock time for a quiet-hours end stored as minutes after midnight.
    private func time(_ key: WritableKeyPath<RingPolicy, Int>) -> Binding<Date> {
        Binding(
            get: {
                let minutes = model.ringPolicy[keyPath: key]
                return Calendar.current.date(bySettingHour: minutes / 60, minute: minutes % 60, second: 0, of: Date()) ?? Date()
            },
            set: { date in
                let parts = Calendar.current.dateComponents([.hour, .minute], from: date)
                model.ringPolicy[keyPath: key] = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
            }
        )
    }
}

/// A label and a value on one row; LabeledContent needs iOS 16.
private struct InfoRow: View {
    let title: String
    let value: String

    var body: some View {
        HStack {
            Text(title)
            Spacer(minLength: 12)
            Text(value)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.trailing)
        }
    }
}

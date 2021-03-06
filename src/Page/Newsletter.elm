module Page.Newsletter exposing (Model, Msg(..), init, update, view)

import Data.Kinto exposing (Contact, KintoData(..), emptyContact)
import Data.Session exposing (Session)
import Html as H
import Html.Attributes as HA
import Html.Events as HE
import Kinto
import Page.Common.Components
import Page.Common.Notifications as Notifications
import Random
import Random.Char
import Random.String
import Request.KintoContact
import Route


type alias Model =
    { title : String
    , contact : Contact
    , newContactKintoData : KintoData Contact
    , notifications : Notifications.Model
    }


type RandomPassword
    = RandomPassword String


type Msg
    = UpdateContactForm Contact
    | GenerateRandomPassword
    | SubmitNewContact RandomPassword
    | NewContactSubmitted (Result Kinto.Error Contact)
    | NotificationMsg Notifications.Msg


init : Session -> ( Model, Cmd Msg )
init session =
    ( { title = "Inscrivez-vous à notre infolettre"
      , contact = emptyContact
      , newContactKintoData = NotRequested
      , notifications = Notifications.init
      }
    , Cmd.none
    )


update : Session -> Msg -> Model -> ( Model, Cmd Msg )
update session msg model =
    case msg of
        UpdateContactForm contact ->
            ( { model | contact = contact }, Cmd.none )

        GenerateRandomPassword ->
            ( model, generateRandomPassword )

        SubmitNewContact (RandomPassword randomString) ->
            ( { model | newContactKintoData = Requested }
            , Request.KintoContact.submitContact session.kintoURL model.contact randomString NewContactSubmitted
            )

        NewContactSubmitted (Ok contact) ->
            ( { model
                | newContactKintoData = Received contact
                , contact = emptyContact
                , notifications =
                    "Vous êtes maintenant inscrit à l'infolettre !"
                        |> Notifications.addSuccess model.notifications
              }
            , Cmd.none
            )

        NewContactSubmitted (Err error) ->
            ( { model
                | newContactKintoData = NotRequested
                , notifications =
                    Kinto.errorToString error
                        |> Notifications.addError model.notifications
              }
            , Cmd.none
            )

        NotificationMsg notificationMsg ->
            ( { model | notifications = Notifications.update notificationMsg model.notifications }, Cmd.none )


view : Session -> Model -> ( String, List (H.Html Msg) )
view session { title, contact, newContactKintoData, notifications } =
    let
        buttonState =
            if contact.name == "" || contact.email == "" || contact.role == "" then
                Page.Common.Components.Disabled

            else
                case newContactKintoData of
                    Requested ->
                        Page.Common.Components.Loading

                    _ ->
                        Page.Common.Components.NotLoading
    in
    ( title
    , [ H.div [ HA.class "hero" ]
            [ H.div [ HA.class "hero__container" ]
                [ H.img [ HA.src session.staticFiles.logo_ca12, HA.class "hero__logo" ] []
                , H.h1 [] [ H.text "Inscrivez-vous à notre infolettre" ]
                , H.p [] [ H.text "Tenez-vous au courant des nouvelles vidéos et de l'actualité du projet !" ]
                ]
            ]
      , H.div [ HA.class "main" ]
            [ H.map NotificationMsg (Notifications.view notifications)
            , H.div [ HA.class "section section-white" ]
                [ H.div [ HA.class "container" ]
                    [ H.form [ HE.onSubmit GenerateRandomPassword ]
                        [ formInput
                            "nom"
                            "text"
                            "Nom*"
                            "Votre nom"
                            contact.name
                            (\name -> UpdateContactForm { contact | name = name })
                        , formInput
                            "email"
                            "email"
                            "Email*"
                            "Votre adresse email"
                            contact.email
                            (\email -> UpdateContactForm { contact | email = email })
                        , H.div
                            [ HA.class "form__group" ]
                            [ H.label [ HA.for "role" ]
                                [ H.text "Role*" ]
                            , H.select
                                [ HA.id "role"
                                , HA.value contact.role
                                , Page.Common.Components.onChange
                                    (\role ->
                                        UpdateContactForm { contact | role = role }
                                    )
                                ]
                                [ H.option [] []
                                , H.option [ HA.value "CP" ] [ H.text "Enseignant en CP" ]
                                , H.option [ HA.value "CE1" ] [ H.text "Enseignant en CE1" ]
                                , H.option [ HA.value "Formateur" ] [ H.text "Formateur" ]
                                ]
                            ]
                        , Page.Common.Components.submitButton "M'inscrire à l'infolettre" buttonState
                        , H.p []
                            [ H.text "En renseignant votre nom et votre adresse email, vous acceptez de recevoir des informations ponctuelles par courrier électronique et vous prenez connaissance de notre "
                            , H.a [ Route.href Route.PrivacyPolicy ] [ H.text "politique de confidentialité" ]
                            , H.text "."
                            ]
                        , H.p []
                            [ H.text "Vous pouvez vous désinscrire à tout moment en nous contactant à l'adresse "
                            , H.a [ HA.href "mailto:classea12@education.gouv.fr?subject=désinscription infolettre" ] [ H.text "classea12@education.gouv.fr" ]
                            , H.text "."
                            ]
                        ]
                    ]
                ]
            ]
      ]
    )


formInput : String -> String -> String -> String -> String -> (String -> msg) -> H.Html msg
formInput id type_ label placeholder value onInput =
    H.div
        [ HA.class "form__group" ]
        [ H.label [ HA.for id ]
            [ H.text label ]
        , H.input
            [ HA.id id
            , HA.type_ type_
            , HA.placeholder placeholder
            , HA.value value
            , HE.onInput onInput
            ]
            []
        ]


generateRandomPassword : Cmd Msg
generateRandomPassword =
    Random.generate
        SubmitNewContact
        (Random.String.string 20 Random.Char.latin
            |> Random.map RandomPassword
        )
